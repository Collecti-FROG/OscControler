# File: test_osc_feedback.py
from pythonosc.udp_client import SimpleUDPClient
import time
import sys

# Simulation d'un retour Resolume
# Doit être envoyé au port 7001 du PC où tourne bridge.py
TARGET_IP = "127.0.0.1"
TARGET_PORT = 7001

client = SimpleUDPClient(TARGET_IP, TARGET_PORT)

print(f"📡 Simulation Resolume démarrée vers {TARGET_IP}:{TARGET_PORT}")
print("Envoi de signaux de test toutes les 2 secondes...")

try:
    while True:
        addr = "/composition/layers/1/clips/1/active"
        print(f"🚀 Envoi : {addr} (val: 1.0)")
        client.send_message(addr, 1.0)
        time.sleep(1)
        client.send_message(addr, 0.0)
        time.sleep(1)
except KeyboardInterrupt:
    print("\nArrêt de la simulation.")
