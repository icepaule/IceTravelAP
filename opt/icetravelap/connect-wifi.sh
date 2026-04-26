#!/bin/bash
# Direct WLAN connect to specific SSID - bypasses LAN-first logic
SSID="$1"
PSK="$2"
LOG="/var/log/icetravelap/wifi-manager.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [connect-wifi] $1" >> "$LOG"; }

[ -z "$SSID" ] || [ -z "$PSK" ] && { log "Missing SSID or PSK"; exit 1; }

log "Direct connect requested: $SSID"

# Kill any existing wpa_supplicant on wlan1
killall wpa_supplicant 2>/dev/null
killall dhclient 2>/dev/null
sleep 1

# Bring wlan1 up
ip link set wlan1 up 2>/dev/null
sleep 2

# Generate config and connect
wpa_passphrase "$SSID" "$PSK" > /tmp/wpa_supplicant_wlan1.conf
wpa_supplicant -B -i wlan1 -c /tmp/wpa_supplicant_wlan1.conf
sleep 6

if ! iw dev wlan1 link 2>/dev/null | grep -q "Connected"; then
    log "Failed to associate with $SSID"
    exit 2
fi

# DHCP
dhclient -v wlan1 2>/dev/null || dhcpcd wlan1 2>/dev/null
sleep 3

if ! ip addr show wlan1 | grep -q "inet "; then
    log "No IP on wlan1 after DHCP"
    exit 3
fi

log "Connected to $SSID, got IP: $(ip -br addr show wlan1 | awk '{print $3}')"

# Lower wlan1 default route metric so it wins over eth0
GW=$(ip route | grep "default via.*dev wlan1" | awk '{print $3}' | head -1)
if [ -n "$GW" ]; then
    ip route del default via "$GW" dev wlan1 2>/dev/null
    ip route add default via "$GW" dev wlan1 metric 50
fi

# Restart WG via new path
wg-quick down wg0 2>/dev/null
/opt/icetravelap/wg-resolve.sh
wg-quick up wg0 2>&1 | tail -3 >> "$LOG"
sleep 6

if wg show wg0 2>/dev/null | grep -q "latest handshake"; then
    log "WireGuard tunnel up via WLAN ($SSID)"
    /opt/icetravelap/notify.sh "tunnel-up" "$SSID" 2>/dev/null &
    exit 0
else
    log "WireGuard tunnel did not handshake"
    exit 4
fi
