#!/bin/bash
set -e

CONFIG_DIR="/etc/icetravelap"
LOG_DIR="/var/log/icetravelap"
KNOWN_NETWORKS="$CONFIG_DIR/known_networks.conf"

# Load admin-overridable config
[ -f /etc/icetravelap/icetravelap.conf ] && source /etc/icetravelap/icetravelap.conf
HOTSPOT_SSID="${HOTSPOT_SSID:-IceTravelAP}"
HOTSPOT_PSK="${HOTSPOT_PSK:-changeme123}"
SETUP_AP_SSID="${SETUP_AP_SSID:-IceTravelAP-Setup}"
SETUP_AP_PSK="${SETUP_AP_PSK:-changeme123}"
HOTSPOT_IP="10.3.141.1"
SETUP_IP="192.168.99.1"
LAN_TIMEOUT=30

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_DIR/wifi-manager.log"
    echo "$1"
}

start_hotspot() {
    local ssid="$1"
    local psk="$2"
    local ip="$3"
    local range_start="${ip%.*}.10"
    local range_end="${ip%.*}.50"

    log "Starting hotspot: $ssid on wlan0 ($ip)"

    ip addr flush dev wlan0 2>/dev/null || true
    ip addr add "$ip/24" dev wlan0
    ip link set wlan0 up

    cat > /tmp/hostapd.conf << EOF
interface=wlan0
driver=nl80211
ssid=$ssid
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=$psk
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

    cat > /tmp/dnsmasq-hotspot.conf << EOF
interface=wlan0
dhcp-range=${range_start},${range_end},255.255.255.0,24h
dhcp-leasefile=/var/lib/misc/dnsmasq.leases
bind-interfaces
server=8.8.8.8
server=1.1.1.1
EOF

    killall hostapd 2>/dev/null || true
    killall dnsmasq 2>/dev/null || true
    sleep 1

    hostapd -B /tmp/hostapd.conf
    dnsmasq -C /tmp/dnsmasq-hotspot.conf

    log "Hotspot $ssid started"
}

# Try LAN (eth0) first - return 0 if usable upstream found
try_lan() {
    log "Checking LAN (eth0)..."
    ip link set eth0 up 2>/dev/null || true

    local elapsed=0
    while [ $elapsed -lt $LAN_TIMEOUT ]; do
        if ip link show eth0 2>/dev/null | grep -q "LOWER_UP"; then
            # Has carrier - check for IP
            if ip addr show eth0 | grep -q "inet "; then
                # Has IP - test internet
                if ping -c 1 -W 2 -I eth0 8.8.8.8 >/dev/null 2>&1; then
                    log "LAN upstream OK (eth0)"
                    return 0
                fi
            else
                # Try DHCP
                dhclient -v eth0 2>/dev/null || true
            fi
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    log "LAN unavailable after ${LAN_TIMEOUT}s"
    return 1
}

scan_and_connect() {
    log "Scanning for known networks on wlan1..."

    ip link set wlan1 up 2>/dev/null || true
    sleep 2

    local scan_results
    scan_results=$(iw dev wlan1 scan 2>/dev/null | grep -oP 'SSID: \K.*')

    if [ -z "$scan_results" ]; then
        log "No networks found in scan"
        return 1
    fi

    while IFS="|" read -r ssid psk; do
        [[ "$ssid" =~ ^#.*$ ]] && continue
        [ -z "$ssid" ] && continue

        if echo "$scan_results" | grep -qF "$ssid"; then
            log "Found known network: $ssid - attempting connection..."

            wpa_passphrase "$ssid" "$psk" > /tmp/wpa_supplicant_wlan1.conf
            killall wpa_supplicant 2>/dev/null || true
            sleep 1
            wpa_supplicant -B -i wlan1 -c /tmp/wpa_supplicant_wlan1.conf
            sleep 5
            dhclient -v wlan1 2>/dev/null || dhcpcd wlan1 2>/dev/null
            sleep 3

            if iw dev wlan1 link 2>/dev/null | grep -q "Connected"; then
                log "Connected to $ssid"
                return 0
            else
                log "Failed to connect to $ssid"
                killall wpa_supplicant 2>/dev/null || true
            fi
        fi
    done < "$KNOWN_NETWORKS"

    log "No known network available"
    return 1
}

start_wireguard() {
    log "Starting WireGuard tunnel..."
    /opt/icetravelap/wg-resolve.sh
    wg-quick up wg0 2>&1 | tail -3
    sleep 5
    if wg show wg0 2>/dev/null | grep -q "latest handshake"; then
        log "WireGuard tunnel established"
        /opt/icetravelap/notify.sh "tunnel-up" "auto" 2>/dev/null &
        return 0
    else
        sleep 5
        if wg show wg0 2>/dev/null | grep -q "latest handshake"; then
            log "WireGuard tunnel established (delayed)"
            /opt/icetravelap/notify.sh "tunnel-up" "auto-delayed" 2>/dev/null &
            return 0
        fi
        if ip link show wg0 2>/dev/null | grep -q "UP"; then
            log "WireGuard interface up, no handshake yet"
            return 0
        fi
        log "WireGuard tunnel failed"
        return 1
    fi
}

start_setup_mode() {
    log "Entering setup mode..."
    start_hotspot "$SETUP_AP_SSID" "$SETUP_AP_PSK" "$SETUP_IP"
    log "Setup portal already running at http://$SETUP_IP:8080"
}

### MAIN ###
log "=== IceTravelAP starting ==="

echo 1 > /proc/sys/net/ipv4/ip_forward

# Always start the user-facing hotspot first (clients can connect even before upstream)
start_hotspot "$HOTSPOT_SSID" "$HOTSPOT_PSK" "$HOTSPOT_IP"

# Priority 1: LAN (eth0)
if try_lan; then
    if start_wireguard; then
        log "=== Travel AP operational via LAN ==="
        exit 0
    else
        log "WireGuard failed via LAN - trying WLAN upstream"
    fi
fi

# Priority 2: WLAN known networks
if scan_and_connect; then
    if start_wireguard; then
        log "=== Travel AP operational via WLAN ==="
        exit 0
    else
        log "WireGuard failed - internet available without tunnel"
        exit 0
    fi
fi

# Priority 3: Setup mode
log "No upstream found - starting setup mode"
start_setup_mode
