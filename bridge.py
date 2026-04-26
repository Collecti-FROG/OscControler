# File: bridge.py

import asyncio
import json
import logging
import socket
import threading
import http.server
import socketserver
import os
import subprocess
import re
import time
import urllib.request
import warnings
from collections import deque

# Silence warnings
warnings.simplefilter("ignore")

# Ensure working directory is script location
os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    import qrcode
    import qrcode.image.svg
except ImportError:
    qrcode = None

from pythonosc.udp_client import SimpleUDPClient
import websockets

# Custom Silent HTTP Handler
class SilentHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Silence all GET/POST logs in console

# Configuration
OSC_PORT = 7000
WS_HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 8000

# Logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)
# Silence external noisy libraries
logging.getLogger('websockets').setLevel(logging.ERROR)
logging.getLogger('pythonosc').setLevel(logging.ERROR)

# State
osc_clients = []
connected_clients = set()
global_state = {}
layout_state = {}
# History for OSC Learn (last 5 unique addresses)
address_history = deque(maxlen=5)
# Stats for Learn Stability (address -> [timestamps])
learn_stats = {}
last_learn_time = 0
is_learning = False
last_capture_time = 0 # Lock for 2 seconds after a successful capture
targethost = os.getenv('TARGET_HOST', '127.0.0.1')
localhost = '127.0.0.1'

LAYOUT_FILE = "layout.json"

# Noise filter for Resolume
NOISE_PATTERNS = [
    r"/composition/temp",
    r"/composition/time",
    r"/composition/beats",
    r"/composition/speed",
    r"/composition/datetime",
    r"/fft/",
    r"/video/position",
    r"/transport/position",
    r"/composition/video/resolution",
    r"/composition/link",
    r"/monitor/",
    r"/heartbeat"
]

def is_noise(address):
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, address):
            return True
    return False

# -----------------------------
# Layout Persistence
# -----------------------------
def load_layout():
    global layout_state
    if os.path.exists(LAYOUT_FILE):
        try:
            with open(LAYOUT_FILE, "r", encoding="utf-8") as f:
                layout_state = json.load(f)
        except:
            layout_state = {}

def save_layout():
    try:
        with open(LAYOUT_FILE, "w", encoding="utf-8") as f:
            json.dump(layout_state, f, indent=2)
    except: pass

# -----------------------------
# WebSocket
# -----------------------------
async def send_full_state(ws):
    if global_state:
        await ws.send(json.dumps({"address": "/__full_state__", "state": global_state}))

async def send_full_layout(ws):
    if layout_state:
        await ws.send(json.dumps({"address": "/__full_layout__", "layout": layout_state}))

async def broadcast_layout(exclude_ws=None):
    if not connected_clients: return
    msg = json.dumps({"address": "/__full_layout__", "layout": layout_state})
    tasks = [ws.send(msg) for ws in connected_clients if ws != exclude_ws]
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)

async def handle_ws_connection(websocket):
    global is_learning, layout_state, last_learn_time
    
    client_ip = websocket.remote_address[0]
    logger.info(f"[+] Client connecte : {client_ip}")
    
    connected_clients.add(websocket)
    await send_server_info(websocket)
    await send_full_state(websocket)
    await send_full_layout(websocket)

    loop = asyncio.get_running_loop()

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                address = data.get("address")
                value = data.get("value")

                if address == "/__save_layout__":
                    layout_state = data.get("layout", {})
                    save_layout()
                    await broadcast_layout(exclude_ws=websocket)
                    continue

                if address == "/__request_sync__":
                    await send_full_state(websocket)
                    await send_full_layout(websocket)
                    continue

                if address == "/__start_learn__":
                    is_learning = True
                    last_learn_time = time.time()
                    learn_stats.clear() # Reset frequency stats for fresh capture
                    logger.info("--- MODE LEARN ACTIF ---")
                    continue
                if address == "/__stop_learn__":
                    is_learning = False
                    continue

                if address is None: continue
                global_state[address] = value

                # Broadcast to other WS clients
                msg = json.dumps({"address": address, "value": value, "remote": True})
                tasks = [ws.send(msg) for ws in connected_clients if ws != websocket]
                if tasks: await asyncio.gather(*tasks, return_exceptions=True)

                # Send to OSC (Async-Threaded)
                if not address.startswith('/__'):
                    # Sanitize: split by space and take first part (Resolume copy-paste fix)
                    clean_addr = address.split(' ')[0].strip()
                    # Log removed for performance and console clarity unless it's a manual action
                    # logger.info(f"[OSC OUT] {clean_addr} -> {value}")
                    for client in osc_clients:
                        loop.run_in_executor(None, lambda c=client, a=clean_addr, v=value: c.send_message(a, v))
            except Exception as e:
                logger.error(f"[X] Error WS message: {e}")
    except Exception as e:
        logger.error(f"[X] Connection error: {e}")
    finally:
        connected_clients.remove(websocket)

# -----------------------------
# Resolume REST API Polling
# -----------------------------
async def poll_resolume_selection():
    """Poll Resolume REST API to find the currently selected clip/layer."""
    global is_learning
    last_selection = None
    
    while True:
        if is_learning:
            try:
                # Poll composition JSON from localhost:8080
                with urllib.request.urlopen(f"http://{localhost}:8080/api/v1/composition", timeout=0.5) as response:
                    data = json.loads(response.read().decode())
                    selected_addr = find_selected_osc(data)
                    
                    if selected_addr and selected_addr != last_selection:
                        now = time.time()
                        global last_capture_time
                        if now - last_capture_time > 3.0: # 3 second lock
                            last_selection = selected_addr
                            last_capture_time = now
                            msg = json.dumps({"address": "/__learned_address__", "learned": selected_addr, "source": "REST"})
                            for ws in connected_clients:
                                asyncio.create_task(ws.send(msg))
            except Exception as e:
                pass
        else:
            last_selection = None
        await asyncio.sleep(0.8) # Polling rate

def find_selected_osc(data):
    """Deep search for 'selected': True in Resolume JSON and return OSC address."""
    layers = data.get('layers', [])
    for l_idx, layer in enumerate(layers):
        if layer.get('selected'):
            return f"/composition/layers/{l_idx + 1}/select"
        
        clips = layer.get('clips', [])
        for c_idx, clip in enumerate(clips):
            if clip.get('selected') and clip.get('name', {}).get('value') != "":
                return f"/composition/layers/{l_idx + 1}/clips/{c_idx + 1}/connect"
    return None

async def send_server_info(websocket):
    main_ip, _ = get_windows_ips()
    msg = json.dumps({"address": "/__server_info__", "ip": main_ip, "port": HTTP_PORT})
    await websocket.send(msg)

# -----------------------------
# Network
# -----------------------------
def get_windows_ips():
    try:
        output = subprocess.check_output("ipconfig", encoding="utf-8", errors="ignore")
    except:
        return localhost, [localhost]
    
    adapters = output.split("\n\n")
    valid_ips = []
    main_ip = None
    priority = ["wi-fi", "wifi", "wireless", "ethernet", "lan"]
    blacklist = ["nordlynx", "vpn", "virtual", "vmware", "virtualbox", "loopback", "host-only", "zerotier", "tailscale"]
    
    for adapter in adapters:
        lower = adapter.lower()
        if any(msg in lower for msg in ["mdia dconnect", "media disconnected"]): continue
        if any(b in lower for b in blacklist): continue
        
        match = re.search(r"IPv4[^:]*:\s*([\d\.]+)", adapter)
        if match:
            ip = match.group(1)
            if not ip.startswith("127."):
                # Store IP and calculate a priority score
                score = 0
                if ip.startswith("192.168."): score += 1000
                if ip.startswith("172."): score += 500
                if ip.startswith("10."): score += 100
                
                if any(p in lower for p in priority): score += 50
                
                valid_ips.append((ip, score))
    
    # Sort valid_ips by score
    valid_ips.sort(key=lambda x: x[1], reverse=True)
    
    sorted_ips = [x[0] for x in valid_ips]
    main_ip = sorted_ips[0] if sorted_ips else localhost
    
    return main_ip, sorted_ips

# -----------------------------
# MAIN
# -----------------------------
async def main():
    load_layout()
    main_ip, all_ips = get_windows_ips()
    
    # Clear console for maximum visibility of the header
    os.system('cls' if os.name == 'nt' else 'clear')

    print("\n+" + "-"*60 + "+")
    print("|" + " "*17 + "[START] PROXIMA OSC" + " "*24 + "|")
    print("|" + " "*14 + "  EDITION V9 - OSC LEARN FIX  " + " "*16 + "|")
    print("+" + "-"*60 + "+")
    print(f"| IP PRIORITAIRE : {main_ip:<41} |")
    print(f"| PORT HTTP      : {HTTP_PORT:<41} |")
    print("+" + "-"*60 + "+")
    url = f"http://{main_ip}:{HTTP_PORT}/controller.html"
    print(f"| >>> OUVRIR : {url:<47} |")
    print("+" + "-"*60 + "+")
    print("| AUTRES RESEAUX DETECTES :                                  |")
    for ip in all_ips:
        prefix = "[*] " if ip == main_ip else "[ ] "
        print(f"| {prefix}{ip:<54} |")
    print("+" + "-"*60 + "+")
    print(f"\n  TABLETTE : {url}\n")
    print("  [Ctrl+C pour arreter le serveur]")
    print("-"*62 + "\n")

    global osc_clients
    osc_clients = []
    osc_clients.append(SimpleUDPClient(targethost, OSC_PORT))
    
    for ip in all_ips:
        if ip != localhost:
            osc_clients.append(SimpleUDPClient(ip, OSC_PORT))

    asyncio.create_task(poll_resolume_selection())

    # Periodic console reminder of the access URL (every 30 seconds)
    async def console_heartbeat(url, interval=30):
        while True:
            await asyncio.sleep(interval)
            print(f"\n  [PROXIMA] Acces tablette : {url}")
            print(f"  [Ctrl+C pour arreter]")
    asyncio.create_task(console_heartbeat(f"http://{main_ip}:{HTTP_PORT}/controller.html"))

    # Explicitly bind to 0.0.0.0 for external access with address reuse
    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True
    
    httpd = ReusableTCPServer(("0.0.0.0", HTTP_PORT), SilentHTTPHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def start_udp_server(loop):
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import BlockingOSCUDPServer
        dispatcher = Dispatcher()
        
        def default_handler(address, *args):
            global is_learning, last_learn_time, last_capture_time
            val = args[0] if args else 0.0
            
            if is_noise(address): return

            # Update address history for UI display
            if address not in address_history:
                address_history.appendleft(address)
                if connected_clients:
                    msg_hist = json.dumps({"address": "/__address_history__", "history": list(address_history)})
                    for ws in connected_clients:
                        loop.call_soon_threadsafe(asyncio.create_task, ws.send(msg_hist))

            # -------------------------------------------------------
            # LEARN MODE : Capture OSC "Edit OSC Shortcut" from Resolume
            # When you right-click a param in Resolume > Shortcut > Edit OSC,
            # Resolume SENDS the param address via its OSC OUT port (our port 7001).
            # We capture the FIRST non-streaming message as the learned address.
            # -------------------------------------------------------
            if is_learning:
                now = time.time()
                
                # Enforce 3s lock after a successful capture (anti-bounce)
                if now - last_capture_time < 3.0:
                    pass  # Still accept signal LED, but do not capture
                else:
                    # Track frequency to detect streaming (faders moving = NOT a click)
                    if address not in learn_stats: learn_stats[address] = []
                    learn_stats[address].append(now)
                    learn_stats[address] = learn_stats[address][-5:]
                    
                    # A message is "streaming" if 3+ hits in under 500ms
                    is_stream = (len(learn_stats[address]) >= 3 and
                                 (now - learn_stats[address][-3]) < 0.5)
                    
                    # Priority addresses (clip triggers) ALWAYS captured even if repeated
                    is_priority = any(p in address for p in ["/connect", "/trigger", "/select"])
                    
                    if not is_stream or is_priority:
                        last_capture_time = now
                        last_learn_time = now
                        print(f"  [LEARN] Adresse capturee : {address}")
                        msg = json.dumps({"address": "/__learned_address__", "learned": address, "source": "OSC"})
                        for ws in connected_clients:
                            loop.call_soon_threadsafe(asyncio.create_task, ws.send(msg))
            
            # Always send incoming signal LED to UI
            if connected_clients:
                msg_led = json.dumps({"address": "/__incoming_signal__"})
                for ws in connected_clients:
                    loop.call_soon_threadsafe(asyncio.create_task, ws.send(msg_led))

            global_state[address] = val
            msg = json.dumps({"address": address, "value": val, "remote": True})
            for ws in connected_clients:
                loop.call_soon_threadsafe(asyncio.create_task, ws.send(msg))
        
        dispatcher.set_default_handler(default_handler)
        server = BlockingOSCUDPServer(("0.0.0.0", 7001), dispatcher)
        server.serve_forever()

    threading.Thread(target=start_udp_server, args=(asyncio.get_running_loop(),), daemon=True).start()

    if qrcode:
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(f"http://{main_ip}:{HTTP_PORT}/controller.html")
            qr.make(fit=True)
            img = qr.make_image(image_factory=qrcode.image.svg.SvgImage, fill_color="black", back_color="white")
            img.save("qrcode.svg")
        except: pass

    async with websockets.serve(handle_ws_connection, "0.0.0.0", WS_PORT):
        await asyncio.Future()

    # Keep-Alive: Keep the main loop running forever
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] Fermeture de Proxima...")
    except Exception as e:
        print(f"\n[ERREUR CRITIQUE] Le serveur a plante : {e}")
        import traceback
        traceback.print_exc()
        input("\nAppuyez sur Entree pour quitter...")
