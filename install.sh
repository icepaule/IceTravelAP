#!/bin/bash
# IceTravelAP installer for Travel Pi (Raspberry Pi OS Bookworm Lite)
# Run as root: sudo ./install.sh
set -e

if [ "$EUID" -ne 0 ]; then
    echo "Run as root: sudo ./install.sh"
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== IceTravelAP Installer ==="

# 1. Packages
apt-get update
apt-get install -y --no-install-recommends \
    wireguard wireguard-tools openresolv \
    hostapd dnsmasq iptables iw wpasupplicant \
    python3-flask python3-psutil \
    dnsutils net-tools curl \
    xserver-xorg xinit openbox unclutter chromium-browser x11vnc

# 2. Disable conflicting system services
systemctl stop hostapd dnsmasq wpa_supplicant 2>/dev/null || true
systemctl disable hostapd dnsmasq wpa_supplicant 2>/dev/null || true
systemctl mask wpa_supplicant
systemctl unmask hostapd 2>/dev/null || true

# 3. IP forwarding
echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-icetravelap.conf
sysctl -p /etc/sysctl.d/99-icetravelap.conf

# 4. Directories
mkdir -p /opt/icetravelap /etc/icetravelap /etc/wireguard /var/log/icetravelap

# 5. Copy scripts
install -m 755 "$REPO_DIR/opt/icetravelap/"*.sh "$REPO_DIR/opt/icetravelap/"*.py /opt/icetravelap/

# 6. Prompt for secrets / config
read -p "Hotspot SSID [IceTravelAP]: " HOTSPOT_SSID
HOTSPOT_SSID=${HOTSPOT_SSID:-IceTravelAP}
read -s -p "Hotspot Password (min 8 chars): " HOTSPOT_PSK; echo
read -p "DDNS hostname (e.g. mytravel.duckdns.org): " DDNS_HOST
read -p "Home WireGuard server endpoint IP (resolved fallback): " SERVER_IP
read -p "WireGuard server PUBLIC key: " SERVER_PUB
read -p "Travel Pi WireGuard PRIVATE key: " TRAVEL_PRIV
read -p "Home DNS IP (e.g. AdGuard at 192.168.x.y, leave empty to skip): " HOME_DNS
read -p "Pushover USER key (leave empty to skip): " PO_USER
read -p "Pushover APP token (leave empty to skip): " PO_TOKEN

# 7. Generate icetravelap.conf
cat > /etc/icetravelap/icetravelap.conf << EOF
HOTSPOT_SSID="${HOTSPOT_SSID}"
HOTSPOT_PSK="${HOTSPOT_PSK}"
SETUP_AP_SSID="${HOTSPOT_SSID}-Setup"
SETUP_AP_PSK="${HOTSPOT_PSK}"
EOF
chmod 600 /etc/icetravelap/icetravelap.conf

# 8. WireGuard config
sed -e "s|__TRAVEL_PRIVATE_KEY__|${TRAVEL_PRIV}|" \
    -e "s|__SERVER_PUBLIC_KEY__|${SERVER_PUB}|" \
    -e "s|__SERVER_ENDPOINT_IP__|${SERVER_IP}|" \
    -e "s|__HOME_DNS_IP__|${HOME_DNS:-1.1.1.1}|" \
    "$REPO_DIR/etc/wireguard/wg0.conf.template" > /etc/wireguard/wg0.conf
chmod 600 /etc/wireguard/wg0.conf

# 9. wg-resolve
sed "s|__DDNS_HOSTNAME__|${DDNS_HOST}|" \
    "$REPO_DIR/opt/icetravelap/wg-resolve.sh.template" > /opt/icetravelap/wg-resolve.sh
chmod 755 /opt/icetravelap/wg-resolve.sh

# 10. notify.sh (Pushover)
if [ -n "$PO_USER" ] && [ -n "$PO_TOKEN" ]; then
    sed -e "s|__PUSHOVER_USER__|${PO_USER}|" \
        -e "s|__PUSHOVER_TOKEN__|${PO_TOKEN}|" \
        "$REPO_DIR/opt/icetravelap/notify.sh.template" > /opt/icetravelap/notify.sh
    chmod 755 /opt/icetravelap/notify.sh
else
    cat > /opt/icetravelap/notify.sh << 'EOF'
#!/bin/bash
exit 0
EOF
    chmod 755 /opt/icetravelap/notify.sh
fi

# 11. Known networks placeholder
[ -f /etc/icetravelap/known_networks.conf ] || \
    cp "$REPO_DIR/etc/icetravelap/known_networks.conf.example" /etc/icetravelap/known_networks.conf

# 12. sudoers for portal
cat > /etc/sudoers.d/icetravelap << 'EOF'
pi ALL=(ALL) NOPASSWD: /usr/sbin/iw, /usr/bin/wg, /usr/bin/systemctl restart icetravelap, /opt/icetravelap/connect-wifi.sh
EOF
chmod 440 /etc/sudoers.d/icetravelap

# 13. systemd units
cp "$REPO_DIR/etc/systemd/system/"*.service /etc/systemd/system/

# 14. Kiosk auto-login on tty1
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin pi --noclear %I $TERM
EOF

cat > /home/pi/.bash_profile << 'EOF'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    startx /opt/icetravelap/kiosk.sh -- -nocursor
fi
EOF
cat > /home/pi/.xinitrc << 'EOF'
exec /opt/icetravelap/kiosk.sh
EOF
chown pi:pi /home/pi/.bash_profile /home/pi/.xinitrc
echo "allowed_users=anybody" > /etc/X11/Xwrapper.config

# 15. Enable services
systemctl daemon-reload
systemctl enable icetravelap.service icetravelap-portal.service icetravelap-vnc.service
systemctl enable icetravelap-status.service 2>/dev/null || true

echo ""
echo "=== Installation complete ==="
echo "Next: Add known networks to /etc/icetravelap/known_networks.conf, then reboot."
