#!/bin/bash
# Sets up WireGuard server + DuckDNS updater on the home gateway (e.g. Raspberry Pi running AdGuard)
# Run as root.
set -e
[ "$EUID" -ne 0 ] && { echo "Run as root"; exit 1; }

apt-get update
apt-get install -y wireguard wireguard-tools iptables dnsutils curl

read -p "DuckDNS subdomain (without .duckdns.org): " DUCK_SUB
read -p "DuckDNS token: " DUCK_TOKEN
read -p "Outgoing interface name (e.g. eth0): " WAN_IF

# Generate keys if not present
mkdir -p /etc/wireguard
cd /etc/wireguard
umask 077
[ -f server_private.key ] || wg genkey | tee server_private.key | wg pubkey > server_public.key
[ -f travel_private.key ] || wg genkey | tee travel_private.key | wg pubkey > travel_public.key

cat > /etc/wireguard/wg0.conf << EOF
[Interface]
Address = 10.6.0.1/24
ListenPort = 51820
PrivateKey = $(cat server_private.key)

PostUp = iptables -t nat -A POSTROUTING -s 10.6.0.0/24 -o ${WAN_IF} -j MASQUERADE
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostUp = iptables -A FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -s 10.6.0.0/24 -o ${WAN_IF} -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
PostDown = iptables -D FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT

[Peer]
# Travel Pi
PublicKey = $(cat travel_public.key)
AllowedIPs = 10.6.0.2/32
EOF

# IP forwarding
echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-wireguard.conf
sysctl -p /etc/sysctl.d/99-wireguard.conf

systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0

# DuckDNS updater
mkdir -p /opt/duckdns
cat > /opt/duckdns/duck.sh << EOF
#!/bin/bash
DUCKDNS_IP=\$(dig +short www.duckdns.org @8.8.8.8 | grep -E '^[0-9]' | head -1)
if [ -n "\$DUCKDNS_IP" ]; then
    curl -k -s -o /opt/duckdns/duck.log --resolve "www.duckdns.org:443:\${DUCKDNS_IP}" \
        "https://www.duckdns.org/update?domains=${DUCK_SUB}&token=${DUCK_TOKEN}&ip="
fi
EOF
chmod 700 /opt/duckdns/duck.sh

cat > /etc/systemd/system/duckdns.service << EOF
[Unit]
Description=DuckDNS IP Update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/duckdns/duck.sh
EOF

cat > /etc/systemd/system/duckdns.timer << EOF
[Unit]
Description=DuckDNS IP Update Timer

[Timer]
OnBootSec=60
OnUnitActiveSec=5min
Unit=duckdns.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now duckdns.timer

echo ""
echo "=== Server setup complete ==="
echo ""
echo "Travel Pi config values needed for ./install.sh:"
echo "  Server PUBLIC key:      $(cat /etc/wireguard/server_public.key)"
echo "  Travel Pi PRIVATE key:  $(cat /etc/wireguard/travel_private.key)"
echo "  Endpoint IP fallback:   $(curl -s ifconfig.me)"
echo ""
echo "Don't forget to forward UDP 51820 from your router to this host."
