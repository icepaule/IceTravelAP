#!/usr/bin/env python3
"""IceTravelAP Web Portal - WiFi selection + Status dashboard for kiosk mode"""
from flask import Flask, render_template_string, request, redirect, jsonify
import subprocess
import os
import time
import json
import re

app = Flask(__name__)

@app.after_request
def sanitize_for_screenshot(response):
    """When ?ss=1, replace IPs/MACs/tokens with placeholders for documentation screenshots."""
    if request.args.get("ss") != "1":
        return response
    if not response.content_type or not response.content_type.startswith("text/html"):
        return response
    try:
        body = response.get_data(as_text=True)
        # IPv4 - preserve last octet visually but anonymize
        body = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "███.███.███.███", body)
        # MAC addresses
        body = re.sub(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b", "██:██:██:██:██:██", body)
        # Pushover-like tokens (30 alphanumeric)
        body = re.sub(r"\b[a-z0-9]{30}\b", "█" * 30, body)
        response.set_data(body)
    except Exception:
        pass
    return response
CONFIG_DIR = "/etc/icetravelap"
KNOWN_NETWORKS = os.path.join(CONFIG_DIR, "known_networks.conf")
STATUS_FILE = "/var/log/icetravelap/status.json"

BASE_HTML = """<!DOCTYPE html>
<html><head><title>IceTravelAP</title>
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
{% if refresh %}<meta http-equiv="refresh" content="{{ refresh }}">{% endif %}
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow-x:hidden}
body{font-family:-apple-system,'Segoe UI',sans-serif;background:#1a1a2e;color:#eee;font-size:13px}
.tabs{display:flex;background:#0f3460;position:sticky;top:0;z-index:10}
.tab{flex:1;padding:8px;text-align:center;cursor:pointer;font-size:14px;text-decoration:none;color:#eee;border-bottom:2px solid transparent}
.tab.active{background:#16213e;border-bottom-color:#e94560;color:#e94560;font-weight:bold}
.content{padding:8px 10px}
h1{color:#e94560;margin-bottom:6px;font-size:16px}
.card{background:#16213e;padding:8px 12px;margin:5px 0;border-radius:6px;border-left:3px solid #e94560}
.row{display:flex;justify-content:space-between;align-items:center;margin:3px 0}
.label{color:#a2a2a2;font-size:12px}
.val{font-size:14px;font-weight:bold}
.val.ok{color:#4caf50}
.val.warn{color:#ff9800}
.val.err{color:#f44336}
.net{background:#16213e;padding:8px 12px;margin:4px 0;border-radius:6px;border-left:3px solid #0f3460;cursor:pointer;font-size:13px}
.net:hover{border-left-color:#e94560}
.net.known{border-left-color:#4caf50}
.signal{float:right;color:#a2a2a2;font-size:11px}
.signal.good{color:#4caf50}
.signal.med{color:#ff9800}
.signal.weak{color:#f44336}
input[type=password],input[type=text]{width:100%;padding:6px 8px;margin:4px 0;border:1px solid #0f3460;border-radius:4px;background:#0f3460;color:#eee;font-size:13px}
button{background:#e94560;color:white;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;width:100%;margin-top:4px;font-size:13px;font-weight:bold}
button:hover{background:#c73c54}
.check{margin:4px 0;display:flex;align-items:center;gap:6px;font-size:12px}
.check input{width:16px;height:16px}
.msg{background:#0f3460;padding:8px;border-radius:4px;margin:6px 0;border-left:3px solid #e94560;font-size:13px}
.empty{padding:20px;text-align:center;color:#a2a2a2;font-size:13px}
form{display:none;margin-top:6px;padding-top:6px;border-top:1px solid #0f3460}
.net.expanded form{display:block}
</style>
<style>
.kb{position:fixed;left:0;right:0;bottom:0;background:#0a0a1a;border-top:2px solid #e94560;padding:6px;z-index:100;display:none;user-select:none}
.kb.show{display:block}
.kb-row{display:flex;justify-content:center;margin:3px 0;gap:3px}
.kb-key{flex:1;min-width:0;padding:10px 4px;background:#16213e;color:#eee;border:1px solid #0f3460;border-radius:4px;text-align:center;font-size:14px;font-family:sans-serif;cursor:pointer;touch-action:manipulation}
.kb-key:active{background:#e94560}
.kb-key.wide{flex:2}
.kb-key.xwide{flex:3}
.kb-close{position:absolute;top:4px;right:8px;color:#a2a2a2;font-size:18px;cursor:pointer;background:none;border:none;padding:4px 10px}
body.kb-open{padding-bottom:240px}
</style>
<script>
function toggleNet(el){
    document.querySelectorAll('.net.expanded').forEach(n=>{if(n!==el)n.classList.remove('expanded')});
    el.classList.toggle('expanded');
}
let kbActiveInput=null,kbShift=false;
const KB_LAYOUT_LOWER=[['1','2','3','4','5','6','7','8','9','0'],['q','w','e','r','t','z','u','i','o','p'],['a','s','d','f','g','h','j','k','l','-'],['shift','y','x','c','v','b','n','m','.','back'],['123','sym','space','@','enter']];
const KB_LAYOUT_UPPER=[['1','2','3','4','5','6','7','8','9','0'],['Q','W','E','R','T','Z','U','I','O','P'],['A','S','D','F','G','H','J','K','L','_'],['shift','Y','X','C','V','B','N','M',',','back'],['123','sym','space','@','enter']];
const KB_LAYOUT_SYM=[['!','"','§','$','%','&','/','(',')','='],['?','#','+','*','~',';',':','{','}','[',']'],['<','>','|','\\','/','-','_','.',','],['shift','€','ß','ä','ö','ü','`','^','back'],['abc','sym','space','@','enter']];
function kbBuild(layout){
    const kb=document.getElementById('kb');kb.innerHTML='<button class="kb-close" onclick="kbHide()">✕</button>';
    layout.forEach(row=>{const r=document.createElement('div');r.className='kb-row';row.forEach(k=>{const b=document.createElement('button');b.className='kb-key';b.type='button';let lbl=k;if(k==='space'){b.className+=' xwide';lbl='␣ Leerzeichen';}else if(['enter','back','shift','123','abc','sym'].includes(k)){b.className+=' wide';if(k==='enter')lbl='⏎';if(k==='back')lbl='⌫';if(k==='shift')lbl=kbShift?'⇧':'⇧';}b.textContent=lbl;b.onclick=(e)=>{e.preventDefault();kbPress(k);};r.appendChild(b);});kb.appendChild(r);});
}
function kbPress(k){if(!kbActiveInput)return;const v=kbActiveInput.value;if(k==='back'){kbActiveInput.value=v.slice(0,-1);}else if(k==='space'){kbActiveInput.value=v+' ';}else if(k==='enter'){kbActiveInput.form&&kbActiveInput.form.submit();return;}else if(k==='shift'){kbShift=!kbShift;kbBuild(kbShift?KB_LAYOUT_UPPER:KB_LAYOUT_LOWER);}else if(k==='sym'){kbBuild(KB_LAYOUT_SYM);}else if(k==='123'||k==='abc'){kbShift=false;kbBuild(KB_LAYOUT_LOWER);}else{kbActiveInput.value=v+k;}}
function kbShow(input){kbActiveInput=input;kbShift=false;kbBuild(KB_LAYOUT_LOWER);document.getElementById('kb').classList.add('show');document.body.classList.add('kb-open');}
function kbHide(){document.getElementById('kb').classList.remove('show');document.body.classList.remove('kb-open');kbActiveInput=null;}
document.addEventListener('DOMContentLoaded',()=>{document.querySelectorAll('input[type=password],input[type=text]').forEach(i=>{i.addEventListener('focus',()=>kbShow(i));i.addEventListener('click',()=>kbShow(i));});});
</script>
</head><body>
<div class="tabs">
    <a href="/" class="tab {% if page=='status' %}active{% endif %}">Status</a>
    <a href="/wifi" class="tab {% if page=='wifi' %}active{% endif %}">WLAN</a>
    <a href="/known" class="tab {% if page=='known' %}active{% endif %}">Bekannt</a>
    <a href="/diag" class="tab {% if page=='diag' %}active{% endif %}">Diag</a>
</div>
<div class="content">
{{ body|safe }}
</div>
<div id="kb" class="kb"></div>
</body></html>"""

def render(page, body, refresh=0):
    return render_template_string(BASE_HTML, page=page, body=body, refresh=refresh)

def get_clients():
    """Return list of {mac, ip, name} for stations connected to wlan0."""
    macs = []
    try:
        out = subprocess.run(["sudo","iw","dev","wlan0","station","dump"],capture_output=True,text=True,timeout=3).stdout
        for line in out.split("\n"):
            if line.startswith("Station "):
                macs.append(line.split()[1].lower())
    except Exception:
        return []

    # Read DHCP leases from dnsmasq
    leases = {}
    try:
        with open("/var/lib/misc/dnsmasq.leases") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    # ts mac ip name client_id
                    mac = parts[1].lower()
                    ip = parts[2]
                    name = parts[3] if parts[3] != "*" else ""
                    leases[mac] = (ip, name)
    except Exception:
        pass

    # ARP table fallback
    arp = {}
    try:
        with open("/proc/net/arp") as f:
            next(f)
            for line in f:
                p = line.split()
                if len(p) >= 6 and p[5] == "wlan0":
                    arp[p[3].lower()] = p[0]
    except Exception:
        pass

    clients = []
    for mac in macs:
        ip, name = leases.get(mac, (arp.get(mac, "?"), ""))
        clients.append({"mac": mac, "ip": ip, "name": name or "—"})
    return clients

def get_upstream_info():
    """Return dict with upstream type, ssid/iface, signal. Uses WG endpoint route to determine actual upstream."""
    info = {"type":"none","detail":"","signal":0}

    # Determine which interface carries WG endpoint traffic (real upstream)
    upstream_iface = None
    try:
        wg_out = subprocess.run(["sudo","wg","show","wg0","endpoints"],capture_output=True,text=True,timeout=2).stdout
        endpoint_ip = None
        for line in wg_out.split("\n"):
            parts = line.split()
            if len(parts) >= 2 and ":" in parts[1]:
                endpoint_ip = parts[1].rsplit(":",1)[0]
                break
        if endpoint_ip:
            r = subprocess.run(["ip","route","get",endpoint_ip],capture_output=True,text=True,timeout=2).stdout
            for tok in r.split():
                if tok in ("eth0","wlan1"):
                    upstream_iface = tok
                    break
    except Exception:
        pass

    # Fallback: check link state directly
    def has_link(iface):
        try:
            out = subprocess.run(["ip","-br","link","show",iface],capture_output=True,text=True,timeout=2).stdout
            return "LOWER_UP" in out or " UP " in out
        except Exception: return False

    def has_ip(iface):
        try:
            out = subprocess.run(["ip","-4","-br","addr","show",iface],capture_output=True,text=True,timeout=2).stdout
            return "inet " in out or any(p.startswith("1") or p.startswith("2") for p in out.split() if "." in p)
        except Exception: return False

    if upstream_iface == "eth0" or (upstream_iface is None and has_link("eth0") and has_ip("eth0")):
        try:
            out = subprocess.run(["ip","-4","-br","addr","show","eth0"],capture_output=True,text=True,timeout=2).stdout
            ip = out.split()[2].split("/")[0] if len(out.split()) >= 3 else ""
            info["type"] = "lan"
            info["detail"] = f"eth0 ({ip})"
            return info
        except Exception: pass

    try:
        out = subprocess.run(["sudo","iw","dev","wlan1","link"],capture_output=True,text=True,timeout=3).stdout
        if "Connected" in out:
            info["type"] = "wlan"
            for line in out.split("\n"):
                line = line.strip()
                if line.startswith("SSID:"):
                    info["detail"] = line.split(":",1)[1].strip()
                elif line.startswith("signal:"):
                    try: info["signal"] = int(line.split(":")[1].strip().split()[0])
                    except: pass
    except Exception:
        pass
    return info

def get_status():
    s = {"tunnel":"unknown","clients":0,"upstream":"none","upstream_ssid":"","upstream_signal":0,
         "rx_bytes":0,"tx_bytes":0,"uptime":0,"hotspot_ssid":""}
    try:
        # WireGuard status
        wg = subprocess.run(["sudo","wg","show","wg0"],capture_output=True,text=True,timeout=3).stdout
        if "latest handshake" in wg:
            s["tunnel"] = "connected"
            for line in wg.split("\n"):
                if "transfer:" in line:
                    parts = line.split("transfer:")[1].strip()
                    rx = parts.split("received")[0].strip()
                    tx = parts.split(",")[1].strip().split("sent")[0].strip()
                    s["rx_bytes"] = rx
                    s["tx_bytes"] = tx
        elif "interface:" in wg:
            s["tunnel"] = "up"
        else:
            s["tunnel"] = "down"
    except Exception:
        pass
    # Connected clients on wlan0
    try:
        out = subprocess.run(["sudo","iw","dev","wlan0","station","dump"],capture_output=True,text=True,timeout=3).stdout
        s["clients"] = out.count("Station ")
    except Exception:
        pass
    # Hotspot SSID
    try:
        out = subprocess.run(["sudo","iw","dev","wlan0","info"],capture_output=True,text=True,timeout=3).stdout
        for line in out.split("\n"):
            if "ssid" in line:
                s["hotspot_ssid"] = line.strip().split("ssid",1)[1].strip()
    except Exception:
        pass
    # Upstream wlan1
    try:
        out = subprocess.run(["sudo","iw","dev","wlan1","link"],capture_output=True,text=True,timeout=3).stdout
        if "Connected" in out:
            for line in out.split("\n"):
                line = line.strip()
                if line.startswith("SSID:"):
                    s["upstream_ssid"] = line.split(":",1)[1].strip()
                    s["upstream"] = "connected"
                elif line.startswith("signal:"):
                    try: s["upstream_signal"] = int(line.split(":")[1].strip().split()[0])
                    except: pass
    except Exception:
        pass
    # Uptime
    try:
        with open("/proc/uptime") as f:
            s["uptime"] = int(float(f.read().split()[0]))
    except Exception:
        pass
    return s

def fmt_bytes(b):
    try: b = int(b)
    except: return str(b)
    for u in ["B","KB","MB","GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def fmt_uptime(s):
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m"

def signal_class(dbm):
    if dbm >= -60: return "good"
    if dbm >= -75: return "med"
    return "weak"

def known_networks():
    nets = []
    try:
        with open(KNOWN_NETWORKS) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                if "|" in line:
                    ssid, psk = line.split("|",1)
                    nets.append({"ssid":ssid,"psk":psk})
    except Exception: pass
    return nets

@app.route("/")
def status_page():
    s = get_status()
    clients = get_clients()
    upstream = get_upstream_info()
    known_ssids = {n["ssid"] for n in known_networks()}

    tunnel_class = {"connected":"ok","up":"warn","down":"err"}.get(s["tunnel"],"warn")
    upstream_class = "ok" if upstream["type"] != "none" else "err"
    upstream_label = {"lan":"LAN","wlan":"WLAN","none":"NONE"}[upstream["type"]]

    client_rows = ""
    if clients:
        for c in clients:
            client_rows += f'<div class="row" style="font-size:11px;border-top:1px solid #0f3460;padding-top:4px"><span class="label" style="font-family:monospace">{c["ip"]}</span><span style="color:#a2a2a2;font-family:monospace;font-size:10px">{c["mac"]}</span><span class="val" style="font-size:11px">{c["name"]}</span></div>'

    body = f"""
<h1>Status</h1>
<div class="card">
    <div class="row"><span class="label">WireGuard Tunnel</span>
        <span class="val {tunnel_class}">{s['tunnel'].upper()}</span></div>
    <div class="row"><span class="label">Daten empfangen</span>
        <span class="val">{fmt_bytes(s['rx_bytes']) if isinstance(s['rx_bytes'],int) or s['rx_bytes'].isdigit() else s['rx_bytes']}</span></div>
    <div class="row"><span class="label">Daten gesendet</span>
        <span class="val">{fmt_bytes(s['tx_bytes']) if isinstance(s['tx_bytes'],int) or s['tx_bytes'].isdigit() else s['tx_bytes']}</span></div>
</div>
<div class="card">
    <div class="row"><span class="label">Hotspot ({s['hotspot_ssid'] or 'wlan0'})</span>
        <span class="val ok">{len(clients)} Clients</span></div>
    {client_rows}
</div>
<div class="card">
    <div class="row"><span class="label">Upstream</span>
        <span class="val {upstream_class}">{upstream_label}</span></div>
    {'<div class="row"><span class="label">'+("Interface" if upstream["type"]=="lan" else "SSID")+'</span><span class="val">'+upstream["detail"]+'</span></div>' if upstream["detail"] else ''}
    {'<div class="row"><span class="label">Signal</span><span class="val '+signal_class(upstream["signal"])+'">'+str(upstream["signal"])+' dBm</span></div>' if upstream["signal"] else ''}
</div>
<div class="card">
    <div class="row"><span class="label">Uptime</span>
        <span class="val">{fmt_uptime(s['uptime'])}</span></div>
    <div class="row"><span class="label">Bekannte Netze</span>
        <span class="val">{len(known_ssids)}</span></div>
</div>
"""
    return render("status", body, refresh=5)

@app.route("/wifi")
def wifi_page():
    networks = []
    try:
        result = subprocess.run(["sudo","iw","dev","wlan1","scan"],capture_output=True,text=True,timeout=15)
        current = {}
        for line in result.stdout.split("\n"):
            line_s = line.strip()
            if line.startswith("BSS "):
                if current.get("ssid"): networks.append(current)
                current = {"ssid":"","signal":-100,"secure":True}
            elif line_s.startswith("SSID:"):
                current["ssid"] = line_s.split(":",1)[1].strip()
            elif line_s.startswith("signal:"):
                try: current["signal"] = float(line_s.split(":")[1].strip().split()[0])
                except: pass
            elif "Privacy" in line_s and "capability" in line_s:
                pass
        if current.get("ssid"): networks.append(current)
    except Exception as e:
        pass

    networks.sort(key=lambda x:x["signal"],reverse=True)
    seen = set(); unique = []
    for n in networks:
        if n["ssid"] and n["ssid"] not in seen:
            seen.add(n["ssid"]); unique.append(n)

    known_ssids = {k["ssid"] for k in known_networks()}

    items = []
    for i,n in enumerate(unique):
        is_known = n["ssid"] in known_ssids
        sig_class = signal_class(int(n["signal"]))
        items.append(f"""
<div class="net {'known' if is_known else ''}" onclick="toggleNet(this)">
    <span class="signal {sig_class}">{int(n['signal'])} dBm</span>
    <strong>{n['ssid']}</strong> {'<span style="color:#4caf50">[gespeichert]</span>' if is_known else ''}
    <form method="post" action="/connect" onclick="event.stopPropagation()">
        <input type="hidden" name="ssid" value="{n['ssid']}">
        <input type="password" name="password" placeholder="{'Passwort (gespeichert)' if is_known else 'WLAN-Passwort'}" {'' if is_known else 'required'}>
        <div class="check"><input type="checkbox" name="save" id="save_{i}" {'checked' if not is_known else ''}>
        <label for="save_{i}">Als bekanntes Netzwerk speichern</label></div>
        <button type="submit">Verbinden</button>
    </form>
</div>""")

    body = '<h1 style="display:flex;justify-content:space-between;align-items:center">WLAN auswählen<a href="/wifi" style="background:#e94560;color:white;padding:4px 10px;border-radius:4px;font-size:12px;text-decoration:none">↻ Scan</a></h1>' + ("".join(items) if items else '<div class="empty">Keine Netzwerke gefunden. Tippe ↻ Scan zum erneuten Suchen.</div>')
    return render("wifi", body)

@app.route("/connect", methods=["POST"])
def connect():
    ssid = request.form.get("ssid","")
    password = request.form.get("password","")
    save = request.form.get("save")
    nets = known_networks()
    if not password:
        for k in nets:
            if k["ssid"] == ssid: password = k["psk"]; break
    if save and ssid and password and not any(k["ssid"]==ssid for k in nets):
        with open(KNOWN_NETWORKS,"a") as f: f.write(f"{ssid}|{password}\n")
    if ssid and password:
        try:
            subprocess.Popen(["sudo","/opt/icetravelap/connect-wifi.sh", ssid, password],start_new_session=True)
        except Exception: pass
    body = f'<h1>Verbinde...</h1><div class="msg">Verbinde direkt mit <strong>{ssid}</strong> über wlan1...<br>Tunnel wird neu aufgebaut.<br><br><a href="/" style="color:#e94560">→ Zum Status</a></div><meta http-equiv="refresh" content="25;url=/">'
    return render("wifi", body)

@app.route("/known")
def known_page():
    nets = known_networks()
    items = []
    for n in nets:
        items.append(f"""
<div class="net known">
    <strong>{n['ssid']}</strong>
    <form method="post" action="/forget" style="display:block;margin-top:10px">
        <input type="hidden" name="ssid" value="{n['ssid']}">
        <button type="submit" style="background:#666">Vergessen</button>
    </form>
</div>""")
    body = "<h1>Bekannte Netzwerke</h1>" + ("".join(items) if items else '<div class="empty">Keine bekannten Netzwerke gespeichert.</div>')
    return render("known", body)

@app.route("/forget", methods=["POST"])
def forget():
    ssid = request.form.get("ssid","")
    nets = [n for n in known_networks() if n["ssid"] != ssid]
    with open(KNOWN_NETWORKS,"w") as f:
        f.write("# Format: SSID|Password (one per line)\n")
        for n in nets: f.write(f"{n['ssid']}|{n['psk']}\n")
    return redirect("/known")

def run_check(label, fn):
    try:
        ok, detail = fn()
        return {"label":label,"ok":ok,"detail":detail}
    except Exception as e:
        return {"label":label,"ok":False,"detail":f"Fehler: {e}"}

def check_tunnel():
    out = subprocess.run(["sudo","wg","show","wg0"],capture_output=True,text=True,timeout=3).stdout
    if "latest handshake" in out:
        for line in out.split("\n"):
            if "latest handshake" in line:
                return True, line.split(":",1)[1].strip()
    return False, "Kein Handshake"

def check_dns(host):
    t0 = time.time()
    out = subprocess.run(["getent","ahostsv4",host],capture_output=True,text=True,timeout=5).stdout
    if out.strip():
        ip = out.split()[0]
        return True, f"{ip} ({int((time.time()-t0)*1000)}ms)"
    return False, "Auflösung fehlgeschlagen"

def check_https(url):
    t0 = time.time()
    r = subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}","-m","8","--interface","wg0",url],capture_output=True,text=True,timeout=10)
    code = r.stdout.strip()
    ms = int((time.time()-t0)*1000)
    return code.startswith("2") or code.startswith("3"), f"HTTP {code} ({ms}ms)"

def check_ext_ip():
    r = subprocess.run(["curl","-s","-m","8","--interface","wg0","https://api.ipify.org"],capture_output=True,text=True,timeout=10)
    ip = r.stdout.strip()
    if ip and ip[0].isdigit():
        return True, f"{ip} (sollte deine Heim-IP sein)"
    return False, "Keine Antwort"

def check_udp(host, port):
    t0 = time.time()
    try:
        ip = subprocess.run(["getent","ahostsv4",host],capture_output=True,text=True,timeout=3).stdout.split()[0]
    except Exception:
        return False, "DNS fehlgeschlagen"
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0",0))
        s.settimeout(3)
        s.sendto(b"\x00"*8, (ip, port))
        try:
            data, addr = s.recvfrom(2048)
            return True, f"Antwort von {addr[0]}:{addr[1]} ({int((time.time()-t0)*1000)}ms)"
        except socket.timeout:
            return True, f"UDP gesendet an {ip}:{port} (kein Echo erwartet)"
    finally:
        s.close()

def check_sip(host="sip.t-online.de", port=5060):
    """SIP OPTIONS request via UDP - typical for SIP-trunk reachability."""
    t0 = time.time()
    try:
        ip = subprocess.run(["getent","ahostsv4",host],capture_output=True,text=True,timeout=3).stdout.split()[0]
    except Exception:
        return False, f"DNS für {host} fehlgeschlagen"
    import socket, random
    branch = "z9hG4bK" + str(random.randint(100000,999999))
    callid = str(random.randint(1000000,9999999))
    msg = (
        f"OPTIONS sip:{host} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP icetravelap;branch={branch}\r\n"
        f"Max-Forwards: 70\r\n"
        f"To: <sip:{host}>\r\n"
        f"From: <sip:test@icetravelap>;tag=1\r\n"
        f"Call-ID: {callid}@icetravelap\r\n"
        f"CSeq: 1 OPTIONS\r\n"
        f"Contact: <sip:test@icetravelap>\r\n"
        f"Accept: application/sdp\r\n"
        f"Content-Length: 0\r\n\r\n"
    ).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0",0))
        s.settimeout(4)
        s.sendto(msg, (ip, port))
        data, addr = s.recvfrom(4096)
        ms = int((time.time()-t0)*1000)
        first = data.decode(errors="replace").split("\r\n",1)[0]
        ok = "200" in first or "OK" in first.upper() or first.startswith("SIP/2.0")
        return ok, f"{first} ({ms}ms via {addr[0]})"
    except socket.timeout:
        return False, f"Timeout - keine SIP-Antwort von {ip}:{port}"
    finally:
        s.close()

def check_stun():
    """Get public IP via STUN (Google's public STUN). Detects NAT/CGNAT."""
    import socket, struct, random
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0",0))
        s.settimeout(4)
        tx_id = bytes(random.randint(0,255) for _ in range(12))
        msg = struct.pack(">HHI", 0x0001, 0, 0x2112A442) + tx_id
        s.sendto(msg, ("stun.l.google.com", 19302))
        data, _ = s.recvfrom(2048)
        # Parse XOR-MAPPED-ADDRESS
        i = 20
        while i < len(data):
            attr_type, attr_len = struct.unpack(">HH", data[i:i+4])
            if attr_type == 0x0020:  # XOR-MAPPED-ADDRESS
                family = data[i+5]
                xport = struct.unpack(">H", data[i+6:i+8])[0] ^ 0x2112
                xip_bytes = bytes(b ^ m for b,m in zip(data[i+8:i+12], b"\x21\x12\xa4\x42"))
                ip = ".".join(str(b) for b in xip_bytes)
                return True, f"Öffentliche IP: {ip}:{xport}"
            i += 4 + attr_len
        return False, "Keine MAPPED-ADDRESS in STUN-Antwort"
    except Exception as e:
        return False, f"STUN-Fehler: {e}"
    finally:
        s.close()

def get_remote_access():
    """Return remote-access info: tunnel IP, hotspot IP, LAN IP, SSH/VNC commands."""
    info = {"tunnel_ip":"", "lan_ip":"", "hotspot_ip":""}
    try:
        out = subprocess.run(["ip","-4","-br","addr"],capture_output=True,text=True,timeout=2).stdout
        for line in out.split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                iface = parts[0]
                ip = parts[2].split("/")[0]
                if iface == "wg0": info["tunnel_ip"] = ip
                elif iface == "eth0": info["lan_ip"] = ip
                elif iface == "wlan0": info["hotspot_ip"] = ip
    except Exception:
        pass
    return info

@app.route("/diag")
def diag_page():
    checks = [
        run_check("WireGuard Tunnel", check_tunnel),
        run_check("DNS google.de", lambda: check_dns("www.google.de")),
        run_check("DNS heise.de", lambda: check_dns("www.heise.de")),
        run_check("HTTPS google.de", lambda: check_https("https://www.google.de")),
        run_check("HTTPS heise.de", lambda: check_https("https://www.heise.de")),
        run_check("Externe IP via Tunnel", check_ext_ip),
        run_check("STUN (NAT-Typ)", check_stun),
        run_check("SIP Sipgate (5060)", lambda: check_sip("sipgate.de",5060)),
        run_check("SIP DUS1 (5060)", lambda: check_sip("dus1.sipgate.net",5060)),
    ]
    items = []
    for c in checks:
        cls = "ok" if c["ok"] else "err"
        sym = "✓" if c["ok"] else "✗"
        border = "#4caf50" if c["ok"] else "#f44336"
        items.append(f'<div class="card" style="border-left-color:{border}"><div class="row"><span class="label">{sym} {c["label"]}</span><span class="val {cls}">{"OK" if c["ok"] else "FAIL"}</span></div><div style="font-size:11px;color:#a2a2a2;margin-top:3px">{c["detail"]}</div></div>')

    ra = get_remote_access()
    remote_items = []
    if ra["tunnel_ip"]:
        remote_items.append(f'<div style="font-size:11px;margin:4px 0"><b>Von daheim (über Tunnel):</b><br><code style="background:#0f3460;padding:2px 6px;border-radius:3px">ssh pi@{ra["tunnel_ip"]}</code><br><code style="background:#0f3460;padding:2px 6px;border-radius:3px;margin-top:3px;display:inline-block">vncviewer {ra["tunnel_ip"]}:0</code></div>')
    if ra["lan_ip"]:
        remote_items.append(f'<div style="font-size:11px;margin:4px 0"><b>Im selben LAN:</b><br><code style="background:#0f3460;padding:2px 6px;border-radius:3px">ssh pi@{ra["lan_ip"]}</code></div>')
    if ra["hotspot_ip"]:
        remote_items.append(f'<div style="font-size:11px;margin:4px 0"><b>Über Hotspot ({ra["hotspot_ip"]}):</b><br><code style="background:#0f3460;padding:2px 6px;border-radius:3px">ssh pi@{ra["hotspot_ip"]}</code></div>')
    remote_items.append('<div style="font-size:10px;color:#a2a2a2;margin-top:6px">User: <b>pi</b> / Pass: <b>icetravelap</b><br>VNC startet automatisch wenn Tunnel up (Port 5900)</div>')

    remote_card = ('<div class="card" style="border-left-color:#2196f3"><div class="row"><span class="label"><b>Remote Zugriff</b></span></div>'
                   + "".join(remote_items) + '</div>')

    body = ('<h1 style="display:flex;justify-content:space-between;align-items:center">Diagnose'
            '<a href="/diag" style="background:#e94560;color:white;padding:4px 10px;border-radius:4px;font-size:12px;text-decoration:none">↻ Test</a></h1>'
            + remote_card
            + "".join(items))
    return render("diag", body)

@app.route("/api/status")
def api_status():
    return jsonify(get_status())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
