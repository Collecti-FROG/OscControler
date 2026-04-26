# Proxima OSC

Contrôleur OSC web conçu pour piloter **Resolume Arena** depuis une tablette ou un navigateur.

Un serveur Python (`bridge.py`) fait le pont entre l'interface web et Resolume via OSC (UDP). La surface de contrôle est entièrement personnalisable : PADs, faders, switchs, pad XY, grilles de clips — le tout glissable/redimensionnable en mode édition.

**Fonctionnalités principales :**
- Interface tactile responsive (tablette, smartphone, desktop)
- Mode **OSC Learn** : clic droit sur un paramètre dans Resolume → adresse capturée automatiquement
- Retour d'état en temps réel depuis Resolume (feedback OSC)
- QR code généré automatiquement pour accès mobile
- Mise en page sauvegardée et synchronisée entre tous les clients connectés
- Multi-onglets

**Ports utilisés :**

| Port | Protocole | Rôle |
|------|-----------|------|
| 8000 | TCP | Interface web |
| 8765 | TCP | WebSocket (bridge → navigateur) |
| 7000 | UDP | OSC OUT → Resolume |
| 7001 | UDP | OSC IN ← Resolume |
| 8080 | TCP | API REST Resolume (OSC Learn) |

---

## Prérequis

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [VSCode](https://code.visualstudio.com/) avec l'extension [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)

---

## Ouvrir et lancer le projet

**1. Ouvrir dans le Dev Container**

Ouvrir le dossier dans VSCode, puis quand la notification apparaît :

> *"Reopen in Container"*

Ou via la palette de commandes (`Ctrl+Shift+P`) :

```
Dev Containers: Reopen in Container
```

Le container Docker se construit et les dépendances Python (`python-osc`, `websockets`, `qrcode`) s'installent automatiquement.

**2. Lancer le serveur**

Appuyer sur `F5` (configuration *Run Bridge OSC*) — ou via le terminal intégré :

```bash
python bridge.py
```

Le serveur affiche l'URL d'accès et génère un QR code :

```
+------------------------------------------------------------+
|                 [START] PROXIMA OSC                        |
| IP PRIORITAIRE : 192.168.x.x                               |
| PORT HTTP      : 8000                                       |
| >>> OUVRIR : http://192.168.x.x:8000/controller.html       |
+------------------------------------------------------------+
```

**3. Accéder à l'interface**

- **PC / navigateur** : ouvrir l'URL affichée dans le terminal
- **Tablette / mobile** : scanner le QR code dans le panneau gauche de l'interface

---

## Configuration Resolume Arena

Pour que le feedback OSC fonctionne, configurer Resolume :

- **OSC Output** → `127.0.0.1:7001`
- **OSC Input** → port `7000`
- **API REST** → activer sur le port `8080` (requis pour le mode Learn)
