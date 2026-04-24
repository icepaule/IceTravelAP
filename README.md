# IceTravelAP

# IceTravelAP — Claude Code Implementierungs-Prompt

> **Verwendung:** Dieses Dokument vollständig als Prompt an eine lokale Claude Code Instanz übergeben.
> `claude < IceTravelAP_ClaudeCode_Prompt.md` oder als Kontext-Datei einlesen.

---

## Aufgabe

Implementiere das Projekt **IceTravelAP** vollständig auf einem Raspberry Pi 4 mit Raspberry Pi OS Bookworm Lite (64-bit). Das System verwandelt den Pi in einen Travel-Router, der:

1. Bekannte WLANs automatisch verbindet
2. Bei unbekannter Umgebung einen Setup-AP mit Web-Auswahl-Portal aufbaut
3. Allen Client-Traffic durch einen WireGuard-Tunnel zur Heim-Fritzbox in Deutschland tunnelt
4. Einen eigenen Hotspot bereitstellt (wlan0 = AP, wlan1 = Upstream-Client)
5. Status und Traffic auf einem I2C-OLED-Display (SSD1306, 128×64) anzeigt

---

## Systemvoraussetzungen (vor Ausführung prüfen)

```bash
# Betriebssystem
uname -a                    # Muss: Linux ... aarch64
cat /etc/os-release         # Muss: Bookworm (Debian 12)

# Interfaces
ip link show                # wlan0 (onboard), wlan1 (USB-Adapter) müssen vorhanden sein
i2cdetect -y 1              # OLED muss auf 0x3c erscheinen (I2C muss vorab aktiviert sein)

# Netzwerk
ping -c 2 8.8.8.8           # Internet-Verbindung für apt

# Berechtigungen
whoami                      # Muss: root oder sudo-fähig
```

Falls `wlan1` fehlt: USB-WLAN-Adapter (TP-Link Archer T2U Nano / RTL8811AU) einstecken und Treiber installieren — Anweisungen in Phase 1.

Falls I2C nicht aktiv: `sudo raspi-config nonint do_i2c 0 && sudo reboot`

---

## Konfigurationsvariablen

**Diese Werte vor Ausführung anpassen:**

```bash
# === NETZWERK ===
HOTSPOT_SSID="IceTravelAP"
HOTSPOT_PSK="IceTravel2024!"       # Min. 8 Zeichen
SETUP_AP_SSID="IceTravelAP-Setup"
SETUP_AP_PSK="SetupMode1234"

# === WIREGUARD ===
WG_HOME_ENDPOINT="deinname.duckdns.org"   # DuckDNS-Hostname der Fritzbox/Heim-Pi
WG_HOME_PORT="51820"
WG_HOME_PUBKEY="<PUBLIC_KEY_HEIM_PI>"     # Aus pivpn add output
WG_TRAVEL_PRIVKEY="<PRIVATE_KEY_TRAVEL_PI>"  # Generiert mit: wg genkey
WG_TRAVEL_ADDRESS="10.6.0.2/24"
WG_DNS="10.6.0.1"

# === DISPLAY ===
OLED_WIDTH=128
OLED_HEIGHT=64
OLED_I2C_ADDRESS="0x3c"

# === PFADE ===
PROJECT_DIR="/opt/icetravelap"
CONFIG_DIR="/etc/icetravelap"
LOG_DIR="/var/log/icetravelap"
RUN_DIR="/run/icetravelap"
```

---

## Phase 1: System-Pakete und Treiber

### 1.1 System aktualisieren

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
  wireguard wireguard-tools \
  hostapd dnsmasq \
  network-manager \
  iptables iptables-persistent nftables \
  python3-pip python3-venv python3-dev \
  i2c-tools python3-smbus \
  git curl wget jq \
  dkms build-essential \
  rfkill iw \
  ufw \
  avahi-daemon \
  libopenjp2-7 libtiff6 libfreetype6-dev

# IP-Forwarding permanent aktivieren
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-icetravelap.conf
sudo sysctl -p /etc/sysctl.d/99-icetravelap.conf
```

### 1.2 USB-WLAN-Adapter Treiber (RTL8811AU / TP-Link Archer T2U Nano)

```bash
# Prüfen ob wlan1 bereits vorhanden
if ! ip link show wlan1 &>/dev/null; then
  echo "Installing RTL8811AU driver..."
  git clone https://github.com/aircrack-ng/rtl8812au.git /tmp/rtl8812au
  cd /tmp/rtl8812au
  sudo make dkms_install
  cd ~
fi
```

### 1.3 Interface-Namen fixieren via udev

```bash
# MAC-Adresse des USB-Adapters ermitteln
USB_MAC=$(ip link show | grep -A1 "wlan" | grep -v "wlan0" | grep "link/ether" | awk '{print $2}' | head -1)

sudo tee /etc/udev/rules.d/72-icetravelap-wifi.rules << EOF
# IceTravelAP: USB-WLAN-Adapter immer als wlan1
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="${USB_MAC}", NAME="wlan1"
EOF

sudo udevadm control --reload-rules
```

### 1.4 Verzeichnisstruktur anlegen

```bash
sudo mkdir -p ${PROJECT_DIR}/{scripts,templates}
sudo mkdir -p ${CONFIG_DIR}
sudo mkdir -p ${LOG_DIR}
sudo mkdir -p ${RUN_DIR}

# Initiale Status-Datei
echo '{"mode":"boot","ssid":"","tunnel":"none","clients":0,"time":""}' \
  | sudo tee ${RUN_DIR}/status.json
```

---

## Phase 2: Python-Umgebung und Bibliotheken

```bash
# Virtual Environment für IceTravelAP
sudo python3 -m venv ${PROJECT_DIR}/venv
sudo ${PROJECT_DIR}/venv/bin/pip install --upgrade pip

sudo ${PROJECT_DIR}/venv/bin/pip install \
  flask \
  psutil \
  adafruit-circuitpython-ssd1306 \
  adafruit-blinka \
  pillow \
  RPi.GPIO

# Symlink für einfachen Aufruf
sudo ln -sf ${PROJECT_DIR}/venv/bin/python3 /usr/local/bin/icetravelap-python
```

---

## Phase 3: Konfigurationsdateien anlegen

### 3.1 Bekannte Netzwerke

```bash
sudo tee ${CONFIG_DIR}/known_networks.conf << 'EOF'
# IceTravelAP - Bekannte Netzwerke
# Format: SSID|Passwort (eine Zeile pro Netzwerk)
# Kommentare mit # am Zeilenanfang werden ignoriert
#
# Beispiele:
# MeinHeimnetz|GeheimesPasswort123
# Buero-WLAN|BueroPasswort456
EOF
sudo chmod 640 ${CONFIG_DIR}/known_networks.conf
```

### 3.2 WireGuard-Konfiguration

```bash
sudo tee /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = ${WG_TRAVEL_PRIVKEY}
Address = ${WG_TRAVEL_ADDRESS}
DNS = ${WG_DNS}
PostUp = iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE; \
         iptables -A FORWARD -i wlan0 -o wg0 -j ACCEPT; \
         iptables -A FORWARD -i wg0 -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o wg0 -j MASQUERADE; \
           iptables -D FORWARD -i wlan0 -o wg0 -j ACCEPT; \
           iptables -D FORWARD -i wg0 -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT

[Peer]
PublicKey = ${WG_HOME_PUBKEY}
Endpoint = ${WG_HOME_ENDPOINT}:${WG_HOME_PORT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF
sudo chmod 600 /etc/wireguard/wg0.conf
```

### 3.3 NetworkManager für AP-Modus vorbereiten

```bash
# NetworkManager soll wlan0 für den Hotspot verwalten
sudo tee /etc/NetworkManager/conf.d/icetravelap.conf << 'EOF'
[main]
plugins=ifupdown,keyfile

[ifupdown]
managed=true

[device]
wifi.scan-rand-mac-address=no
EOF

# Feste Hotspot-Verbindung anlegen
sudo nmcli con add type wifi \
  ifname wlan0 \
  con-name "icetravelap-hotspot" \
  ssid "${HOTSPOT_SSID}" \
  mode ap \
  ipv4.method shared \
  ipv4.addresses "10.3.141.1/24" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "${HOTSPOT_PSK}" \
  wifi.band bg \
  wifi.channel 6

sudo nmcli con modify "icetravelap-hotspot" \
  connection.autoconnect no \
  ipv6.method disabled
```

---

## Phase 4: Haupt-Scripts

### 4.1 WiFi-Manager `/opt/icetravelap/scripts/wifi-manager.sh`

```bash
sudo tee ${PROJECT_DIR}/scripts/wifi-manager.sh << 'SCRIPT'
#!/bin/bash
# IceTravelAP WiFi Manager
# Verbindet bekannte Netzwerke, startet WireGuard, oder baut Setup-AP auf

KNOWN_NETS="${CONFIG_DIR}/known_networks.conf"
SETUP_SSID="${SETUP_AP_SSID}"
SETUP_PSK="${SETUP_AP_PSK}"
STATUS_FILE="${RUN_DIR}/status.json"
LOG="${LOG_DIR}/wifi-manager.log"
MAX_CONNECT_WAIT=30

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [wifi-manager] $*" | tee -a "$LOG"
}

update_status() {
  local mode="$1" ssid="$2" tunnel="$3" clients="${4:-0}"
  cat > "$STATUS_FILE" << JSON
{"mode":"${mode}","ssid":"${ssid}","tunnel":"${tunnel}","clients":${clients},"time":"$(date '+%H:%M:%S')"}
JSON
}

start_hotspot() {
  log "Starting client hotspot on wlan0..."
  nmcli con up "icetravelap-hotspot" 2>/dev/null || {
    log "ERROR: Failed to start hotspot"
    return 1
  }
  log "Hotspot ${HOTSPOT_SSID} active on wlan0"
}

start_wireguard() {
  log "Starting WireGuard tunnel to home..."
  systemctl start wg-quick@wg0
  
  local wait=0
  while [ $wait -lt 20 ]; do
    if wg show wg0 2>/dev/null | grep -q "latest handshake"; then
      log "WireGuard tunnel established"
      return 0
    fi
    sleep 2
    ((wait+=2))
  done
  
  # Auch ohne bestätigten Handshake weiter (Pi 4 ist schnell genug)
  if wg show wg0 2>/dev/null | grep -q "peer:"; then
    log "WireGuard peer configured (handshake pending)"
    return 0
  fi
  
  log "WARNING: WireGuard tunnel not confirmed"
  return 1
}

try_known_networks() {
  log "Scanning for known networks on wlan1..."
  
  # Interface aktivieren falls nötig
  nmcli dev set wlan1 managed yes 2>/dev/null
  sleep 2
  
  # Scan starten
  nmcli dev wifi rescan ifname wlan1 2>/dev/null
  sleep 3
  
  local AVAILABLE
  AVAILABLE=$(nmcli -t -f SSID dev wifi list ifname wlan1 2>/dev/null | grep -v "^--$" | sort -u)
  log "Available networks: $(echo "$AVAILABLE" | tr '\n' ',' | sed 's/,$//')"
  
  while IFS='|' read -r ssid psk; do
    # Kommentare und leere Zeilen überspringen
    [[ "$ssid" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$ssid" ]] && continue
    ssid=$(echo "$ssid" | xargs)  # Whitespace trimmen
    
    if echo "$AVAILABLE" | grep -qxF "$ssid"; then
      log "Found known network: '$ssid' - attempting connection..."
      
      nmcli dev wifi connect "$ssid" password "$psk" ifname wlan1 2>/dev/null
      
      local wait=0
      while [ $wait -lt $MAX_CONNECT_WAIT ]; do
        if nmcli -t -f STATE dev show wlan1 2>/dev/null | grep -q "connected"; then
          log "Connected to '$ssid'"
          update_status "connected" "$ssid" "starting"
          return 0
        fi
        sleep 2
        ((wait+=2))
      done
      log "Connection to '$ssid' timed out"
    fi
  done < "$KNOWN_NETS"
  
  return 1
}

start_setup_ap() {
  log "No known network found — starting Setup-AP: ${SETUP_SSID}"
  
  # Setup-AP auf wlan1
  nmcli con delete "icetravelap-setup" 2>/dev/null
  nmcli con add type wifi \
    ifname wlan1 \
    con-name "icetravelap-setup" \
    ssid "${SETUP_SSID}" \
    mode ap \
    ipv4.method shared \
    ipv4.addresses "192.168.99.1/24" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "${SETUP_PSK}" \
    wifi.band bg \
    wifi.channel 11
  
  nmcli con up "icetravelap-setup"
  update_status "setup-ap" "${SETUP_SSID}" "none" 0
  log "Setup-AP active. Connect to '${SETUP_SSID}' → http://192.168.99.1:8080"
  
  # Portal-Service starten
  systemctl start icetravelap-portal
}

# === HAUPTPROGRAMM ===
log "=== IceTravelAP WiFi Manager starting ==="
update_status "boot" "" "none" 0

# Bestehende VPN-Verbindungen trennen
systemctl stop wg-quick@wg0 2>/dev/null

# Hotspot auf wlan0 starten (läuft immer)
start_hotspot

if try_known_networks; then
  # Erfolgreich verbunden: WireGuard starten
  if start_wireguard; then
    update_status "tunnel" "$(nmcli -t -f SSID dev wifi | grep -v '^$' | head -1)" "active" 0
    log "System fully operational with VPN tunnel"
  else
    CONNECTED_SSID=$(nmcli -t -f ACTIVE,SSID dev wifi | grep "^yes" | cut -d: -f2)
    update_status "connected" "$CONNECTED_SSID" "failed" 0
    log "WARNING: Connected to WiFi but WireGuard failed"
  fi
else
  start_setup_ap
fi

log "=== WiFi Manager initialization complete ==="
SCRIPT

sudo chmod +x ${PROJECT_DIR}/scripts/wifi-manager.sh
```

### 4.2 Status-Monitor `/opt/icetravelap/scripts/status-monitor.sh`

```bash
sudo tee ${PROJECT_DIR}/scripts/status-monitor.sh << 'SCRIPT'
#!/bin/bash
# Aktualisiert die Status-JSON-Datei alle 10 Sekunden

STATUS_FILE="${RUN_DIR}/status.json"

while true; do
  # Aktuelle Werte lesen
  if [ -f "$STATUS_FILE" ]; then
    MODE=$(jq -r .mode "$STATUS_FILE" 2>/dev/null)
    SSID=$(jq -r .ssid "$STATUS_FILE" 2>/dev/null)
  fi
  
  # WireGuard-Status
  if wg show wg0 2>/dev/null | grep -q "latest handshake"; then
    TUNNEL="active"
  elif systemctl is-active wg-quick@wg0 &>/dev/null; then
    TUNNEL="no-handshake"
  else
    TUNNEL="down"
  fi
  
  # Verbundene Clients
  CLIENTS=$(iw dev wlan0 station dump 2>/dev/null | grep -c "^Station" || echo 0)
  
  # Status schreiben (nur Tunnel und Clients updaten, Mode/SSID bleibt)
  if [ -f "$STATUS_FILE" ]; then
    jq --arg t "$TUNNEL" --arg c "$CLIENTS" --arg ts "$(date '+%H:%M:%S')" \
      '.tunnel=$t | .clients=($c|tonumber) | .time=$ts' \
      "$STATUS_FILE" > /tmp/status_new.json && mv /tmp/status_new.json "$STATUS_FILE"
  fi
  
  sleep 10
done
SCRIPT

sudo chmod +x ${PROJECT_DIR}/scripts/status-monitor.sh
```

### 4.3 Setup-Portal `/opt/icetravelap/scripts/portal.py`

```bash
sudo tee ${PROJECT_DIR}/scripts/portal.py << 'PYEOF'
#!/usr/bin/env python3
"""IceTravelAP Setup Portal — WLAN-Auswahl im Fallback-Modus"""

import subprocess, json, os, time
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/etc/icetravelap")
RUN_DIR    = os.environ.get("RUN_DIR", "/run/icetravelap")
KNOWN_NETS = os.path.join(CONFIG_DIR, "known_networks.conf")
STATUS_FILE = os.path.join(RUN_DIR, "status.json")

HTML = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IceTravelAP Setup</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
           background: #0a0a0a; color: #e0e0e0; padding: 20px; }
    h1 { color: #4fc3f7; font-size: 1.4em; margin-bottom: 5px; }
    .sub { color: #888; font-size: 0.85em; margin-bottom: 20px; }
    .net { background: #1a1a2e; border: 1px solid #333;
           border-radius: 8px; padding: 12px 15px; margin: 8px 0;
           cursor: pointer; display: flex; justify-content: space-between;
           align-items: center; transition: border-color 0.2s; }
    .net:hover, .net.selected { border-color: #4fc3f7; background: #162030; }
    .net .ssid { font-weight: bold; font-size: 1em; }
    .net .meta { font-size: 0.8em; color: #888; }
    .signal { width: 40px; text-align: right; font-size: 0.85em; }
    .signal.good { color: #66bb6a; }
    .signal.ok   { color: #ffa726; }
    .signal.bad  { color: #ef5350; }
    hr { border: none; border-top: 1px solid #333; margin: 20px 0; }
    input, button { width: 100%; padding: 12px; margin: 6px 0;
                    border-radius: 6px; font-size: 1em; border: 1px solid #444; }
    input { background: #1a1a1a; color: #e0e0e0; }
    input:focus { border-color: #4fc3f7; outline: none; }
    button { background: #0277bd; color: white; border: none; cursor: pointer;
             font-weight: bold; margin-top: 10px; }
    button:hover { background: #01579b; }
    .save-row { display: flex; align-items: center; gap: 10px; margin: 5px 0; }
    .save-row input { width: auto; }
    .status { background: #1a2a1a; border: 1px solid #2a4a2a;
              border-radius: 6px; padding: 10px; margin-top: 20px;
              font-size: 0.85em; color: #a5d6a7; }
    #msg { display: none; background: #1a2a3a; border: 1px solid #1565c0;
           border-radius: 6px; padding: 12px; margin-top: 15px; color: #90caf9; }
  </style>
</head>
<body>
  <h1>❄ IceTravelAP Setup</h1>
  <div class="sub">Wähle ein WLAN oder gib manuell eine SSID ein</div>

  <div id="networks">
    {% for n in networks %}
    <div class="net" onclick="selectNet('{{n.ssid|e}}')">
      <div>
        <div class="ssid">{{n.ssid}}</div>
        <div class="meta">{% if n.secured %}🔒 WPA{% else %}🔓 Offen{% endif %}</div>
      </div>
      <div class="signal {% if n.signal|int > 65 %}good{% elif n.signal|int > 40 %}ok{% else %}bad{% endif %}">
        {{n.signal}}%
      </div>
    </div>
    {% endfor %}
    {% if not networks %}
    <div class="net"><div class="ssid">Keine Netzwerke gefunden</div></div>
    {% endif %}
  </div>

  <hr>

  <form id="connectForm">
    <input id="ssid" name="ssid" placeholder="SSID" required>
    <input id="psk" name="psk" type="password" placeholder="Passwort (leer = offenes Netz)">
    <div class="save-row">
      <input type="checkbox" id="save" name="save" value="1">
      <label for="save">In bekannte Netzwerke speichern</label>
    </div>
    <button type="submit">Verbinden & Tunnel aufbauen</button>
  </form>

  <div id="msg"></div>

  <div class="status">
    <b>Status:</b> {{status.mode}} | Tunnel: {{status.tunnel}} | Clients: {{status.clients}}
  </div>

  <script>
  function selectNet(ssid) {
    document.getElementById('ssid').value = ssid;
    document.querySelectorAll('.net').forEach(n => n.classList.remove('selected'));
    event.currentTarget.classList.add('selected');
  }

  document.getElementById('connectForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const msg = document.getElementById('msg');
    msg.style.display = 'block';
    msg.textContent = '⏳ Verbinde... (kann 30 Sek. dauern)';

    const data = {
      ssid: document.getElementById('ssid').value,
      psk:  document.getElementById('psk').value,
      save: document.getElementById('save').checked
    };

    try {
      const r = await fetch('/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
      });
      const result = await r.json();
      msg.textContent = result.message;
      if (result.success) {
        msg.style.background = '#1a3a1a';
        msg.style.borderColor = '#2a7a2a';
        setTimeout(() => location.reload(), 8000);
      }
    } catch(err) {
      msg.textContent = '❌ Fehler: ' + err.message;
    }
  });
  </script>
</body>
</html>"""

@app.route('/')
def index():
    networks = scan_networks()
    status = get_status()
    return render_template_string(HTML, networks=networks, status=status)

@app.route('/connect', methods=['POST'])
def connect():
    data = request.get_json()
    ssid = data.get('ssid', '').strip()
    psk  = data.get('psk', '').strip()
    save = data.get('save', False)

    if not ssid:
        return jsonify({'success': False, 'message': '❌ SSID darf nicht leer sein'})

    # Verbinden
    cmd = ['nmcli', 'dev', 'wifi', 'connect', ssid, 'ifname', 'wlan1']
    if psk:
        cmd += ['password', psk]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    
    if result.returncode != 0:
        return jsonify({'success': False,
                        'message': f'❌ Verbindung fehlgeschlagen: {result.stderr.strip()}'})

    # In known_networks speichern
    if save and psk:
        try:
            with open(KNOWN_NETS, 'a') as f:
                f.write(f'\n{ssid}|{psk}\n')
        except Exception as e:
            pass  # Nicht kritisch

    # Setup-AP stoppen
    subprocess.run(['nmcli', 'con', 'down', 'icetravelap-setup'], capture_output=True)
    
    # Status updaten
    update_status('connected', ssid, 'starting')
    
    # WireGuard starten (non-blocking)
    subprocess.Popen(['systemctl', 'start', 'wg-quick@wg0'])
    
    return jsonify({'success': True,
                    'message': f'✅ Verbunden mit "{ssid}". WireGuard-Tunnel wird aufgebaut...'})

@app.route('/status')
def status_endpoint():
    return jsonify(get_status())

@app.route('/scan')
def scan():
    return jsonify(scan_networks())

def scan_networks():
    subprocess.run(['nmcli', 'dev', 'wifi', 'rescan', 'ifname', 'wlan1'],
                   capture_output=True, timeout=5)
    time.sleep(2)
    result = subprocess.run(
        ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list', 'ifname', 'wlan1'],
        capture_output=True, text=True)
    nets = []
    seen = set()
    for line in result.stdout.splitlines():
        parts = line.split(':')
        if len(parts) >= 3 and parts[0] and parts[0] not in seen:
            seen.add(parts[0])
            nets.append({
                'ssid': parts[0],
                'signal': parts[1],
                'secured': parts[2] not in ('--', '')
            })
    return sorted(nets, key=lambda x: int(x['signal']) if x['signal'].isdigit() else 0, reverse=True)

def get_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except:
        return {'mode': 'unknown', 'ssid': '', 'tunnel': 'unknown', 'clients': 0, 'time': ''}

def update_status(mode, ssid, tunnel):
    try:
        data = get_status()
        data.update({'mode': mode, 'ssid': ssid, 'tunnel': tunnel,
                     'time': time.strftime('%H:%M:%S')})
        with open(STATUS_FILE, 'w') as f:
            json.dump(data, f)
    except:
        pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
PYEOF

sudo chmod +x ${PROJECT_DIR}/scripts/portal.py
```

### 4.4 OLED-Display `/opt/icetravelap/scripts/display.py`

```bash
sudo tee ${PROJECT_DIR}/scripts/display.py << 'PYEOF'
#!/usr/bin/env python3
"""IceTravelAP OLED Status Display (SSD1306 128x64 I2C)"""

import time, json, os, subprocess
import busio, psutil
from board import SCL, SDA
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306

STATUS_FILE = os.environ.get("RUN_DIR", "/run/icetravelap") + "/status.json"
FONT_PATH   = "/usr/share/fonts/truetype/dejavu/"

# Display initialisieren
i2c  = busio.I2C(SCL, SDA)
disp = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c)
disp.fill(0); disp.show()

W, H = disp.width, disp.height

# Fonts
try:
    f_bold  = ImageFont.truetype(FONT_PATH + "DejaVuSansMono-Bold.ttf", 11)
    f_norm  = ImageFont.truetype(FONT_PATH + "DejaVuSansMono.ttf", 9)
    f_small = ImageFont.truetype(FONT_PATH + "DejaVuSansMono.ttf", 8)
except:
    f_bold = f_norm = f_small = ImageFont.load_default()

def get_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except:
        return {"mode": "boot", "ssid": "", "tunnel": "none", "clients": 0, "time": ""}

def get_wg_traffic():
    """Aktuelle TX/RX-Bytes von wg0 via wg show"""
    try:
        out = subprocess.getoutput("wg show wg0 transfer 2>/dev/null")
        if out:
            parts = out.split()
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    except:
        pass
    return 0, 0

def human_bytes(b):
    if b >= 1_073_741_824: return f"{b/1_073_741_824:.1f}G"
    if b >= 1_048_576:     return f"{b/1_048_576:.1f}M"
    if b >= 1024:          return f"{b/1024:.0f}K"
    return f"{b}B"

def human_speed(bps):
    if bps >= 1_000_000: return f"{bps/1_000_000:.1f}M/s"
    if bps >= 1_000:     return f"{bps/1_000:.0f}K/s"
    return f"{bps}B/s"

def draw_page(page, status, rx_bps, tx_bps, total_rx, total_tx):
    img  = Image.new("1", (W, H))
    d    = ImageDraw.Draw(img)
    mode = status.get("mode", "?")

    if mode == "setup-ap":
        # Setup-Modus anzeigen
        d.text((0,  0), "SETUP MODE",         font=f_bold,  fill=255)
        d.text((0, 14), f"AP: {status.get('ssid','')[:16]}", font=f_small, fill=255)
        d.text((0, 25), f"PW: SetupMode1234",  font=f_small, fill=255)
        d.line([(0, 37), (W, 37)], fill=255)
        d.text((0, 40), "192.168.99.1:8080",   font=f_norm,  fill=255)
        d.text((0, 52), "Verbinde & waehle",   font=f_small, fill=255)

    elif mode == "boot":
        d.text((0, 20), "IceTravelAP",         font=f_bold,  fill=255)
        d.text((0, 36), "Starte...",           font=f_norm,  fill=255)

    elif page == 0:
        # Seite 1: Verbindungs-Status + Live-Traffic
        ssid    = status.get("ssid", "?")[:15]
        tunnel  = status.get("tunnel", "?")
        clients = status.get("clients", 0)
        cpu     = psutil.cpu_percent(interval=0)
        
        tun_sym = "*" if tunnel == "active" else "!"
        d.text((0,  0), f"[{tun_sym}] {ssid}",  font=f_bold,  fill=255)
        d.text((0, 13), f"Tunnel: {tunnel}",     font=f_small, fill=255)
        d.text((0, 22), f"Clients: {clients}  CPU:{cpu:.0f}%", font=f_small, fill=255)
        d.line([(0, 33), (W, 33)], fill=255)
        d.text((0, 36), f"UP:  {human_speed(tx_bps)}",  font=f_norm, fill=255)
        d.text((0, 49), f"DL:  {human_speed(rx_bps)}",  font=f_norm, fill=255)

    elif page == 1:
        # Seite 2: Totale Traffic + RAM
        mem = psutil.virtual_memory()
        d.text((0,  0), "Traffic Gesamt",       font=f_bold,  fill=255)
        d.line([(0, 13), (W, 13)], fill=255)
        d.text((0, 16), f"TX: {human_bytes(total_tx)}", font=f_norm, fill=255)
        d.text((0, 28), f"RX: {human_bytes(total_rx)}", font=f_norm, fill=255)
        d.line([(0, 41), (W, 41)], fill=255)
        d.text((0, 44), f"RAM: {mem.percent:.0f}%  {human_bytes(mem.used)}", font=f_small, fill=255)
        d.text((0, 54), status.get("time", ""),  font=f_small, fill=255)

    return img

# Haupt-Loop
page = 0
prev_rx = prev_tx = 0
prev_time = time.time()

while True:
    try:
        status = get_status()
        
        now = time.time()
        total_rx, total_tx = get_wg_traffic()
        dt = now - prev_time
        
        rx_bps = int((total_rx - prev_rx) / dt) if dt > 0 and prev_rx > 0 else 0
        tx_bps = int((total_tx - prev_tx) / dt) if dt > 0 and prev_tx > 0 else 0
        
        prev_rx, prev_tx, prev_time = total_rx, total_tx, now
        
        img = draw_page(page, status, rx_bps, tx_bps, total_rx, total_tx)
        disp.image(img)
        disp.show()
        
        page = (page + 1) % 2
        time.sleep(4)
        
    except KeyboardInterrupt:
        disp.fill(0); disp.show()
        break
    except Exception as e:
        time.sleep(5)
PYEOF

sudo chmod +x ${PROJECT_DIR}/scripts/display.py
```

---

## Phase 5: Systemd-Services

### 5.1 Alle Service-Dateien anlegen

```bash
# --- WiFi Manager (einmalig beim Boot) ---
sudo tee /etc/systemd/system/icetravelap-wifi.service << EOF
[Unit]
Description=IceTravelAP WiFi Manager
After=network-pre.target NetworkManager.service
Wants=NetworkManager.service
Before=icetravelap-portal.service icetravelap-display.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=${PROJECT_DIR}/scripts/wifi-manager.sh
Environment="CONFIG_DIR=${CONFIG_DIR}"
Environment="RUN_DIR=${RUN_DIR}"
Environment="LOG_DIR=${LOG_DIR}"
Environment="SETUP_AP_SSID=${SETUP_AP_SSID}"
Environment="SETUP_AP_PSK=${SETUP_AP_PSK}"
Environment="HOTSPOT_SSID=${HOTSPOT_SSID}"
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# --- Status Monitor (Dauerlauf) ---
sudo tee /etc/systemd/system/icetravelap-monitor.service << EOF
[Unit]
Description=IceTravelAP Status Monitor
After=icetravelap-wifi.service

[Service]
Type=simple
ExecStart=${PROJECT_DIR}/scripts/status-monitor.sh
Environment="CONFIG_DIR=${CONFIG_DIR}"
Environment="RUN_DIR=${RUN_DIR}"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# --- Setup Portal (bei Bedarf) ---
sudo tee /etc/systemd/system/icetravelap-portal.service << EOF
[Unit]
Description=IceTravelAP Setup Portal
After=network.target

[Service]
Type=simple
ExecStart=${PROJECT_DIR}/venv/bin/python3 ${PROJECT_DIR}/scripts/portal.py
Environment="CONFIG_DIR=${CONFIG_DIR}"
Environment="RUN_DIR=${RUN_DIR}"
WorkingDirectory=${PROJECT_DIR}
Restart=on-failure
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

# --- OLED Display ---
sudo tee /etc/systemd/system/icetravelap-display.service << EOF
[Unit]
Description=IceTravelAP OLED Display
After=icetravelap-wifi.service

[Service]
Type=simple
ExecStart=${PROJECT_DIR}/venv/bin/python3 ${PROJECT_DIR}/scripts/display.py
Environment="RUN_DIR=${RUN_DIR}"
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF

# Services aktivieren
sudo systemctl daemon-reload
sudo systemctl enable \
  icetravelap-wifi \
  icetravelap-monitor \
  icetravelap-portal \
  icetravelap-display
```

---

## Phase 6: Firewall

```bash
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Hotspot-Clients dürfen DNS, DHCP, HTTP
sudo ufw allow in on wlan0 proto udp to any port 53
sudo ufw allow in on wlan0 proto tcp to any port 53
sudo ufw allow in on wlan0 proto udp to any port 67:68
sudo ufw allow in on wlan0 to any port 80,443

# SSH von Upstream (wlan1)
sudo ufw allow in on wlan1 to any port 22

# WireGuard
sudo ufw allow 51820/udp

# Setup-Portal (nur Setup-AP-Interface)
sudo ufw allow in on wlan1 to any port 8080

sudo ufw --force enable
```

---

## Phase 7: Abschluss und Test

```bash
# Neustart
sudo reboot

# Nach Neustart — Tests:
echo "=== WireGuard Status ==="
sudo wg show

echo "=== Hotspot Status ==="
nmcli con show "icetravelap-hotspot"

echo "=== Services ==="
systemctl status icetravelap-wifi icetravelap-monitor icetravelap-display

echo "=== Öffentliche IP (muss deutsche IP sein) ==="
curl -s ifconfig.me

echo "=== Verbundene Clients ==="
iw dev wlan0 station dump | grep Station

echo "=== OLED I2C ==="
i2cdetect -y 1
```

---

## Troubleshooting

```bash
# Logs
journalctl -u icetravelap-wifi -f
journalctl -u icetravelap-display -f
tail -f /var/log/icetravelap/wifi-manager.log

# WireGuard manuell testen
sudo wg-quick up wg0
sudo wg show
curl ifconfig.me   # Muss dt. IP zeigen

# Hotspot manuell testen
nmcli con up icetravelap-hotspot
nmcli con show icetravelap-hotspot

# OLED manuell testen
sudo ${PROJECT_DIR}/venv/bin/python3 ${PROJECT_DIR}/scripts/display.py

# Netzwerk scannen (wlan1)
nmcli dev wifi list ifname wlan1

# Bekanntes Netz hinzufügen (ohne Reboot)
echo "MeineSSID|MeinPasswort" | sudo tee -a /etc/icetravelap/known_networks.conf
sudo systemctl restart icetravelap-wifi
```

---

## Heimnetz-Seite (Kurzreferenz für Fritzbox-Setup)

```bash
# Auf dem Heim-Raspberry-Pi:
curl -L https://install.pivpn.io | bash
# Wählen: WireGuard, Port 51820, DuckDNS-Hostname

pivpn add -n travelpi
# → /etc/wireguard/configs/travelpi.conf (Public/Private Key) auf Travel-Pi übertragen

# Fritzbox: Internet → Freigaben → Portfreigabe
# Protokoll: UDP, Port: 51820, Ziel: [IP des Heim-Pi]

# DuckDNS-Updater (Cronjob auf Heim-Pi)
# */5 * * * * curl -s "https://www.duckdns.org/update?domains=DEINNAME&token=TOKEN&ip=" > /dev/null
```
