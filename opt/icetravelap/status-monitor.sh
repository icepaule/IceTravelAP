#!/bin/bash
STATUS_FILE="/var/log/icetravelap/status.json"
while true; do
    WG_STATUS="down"
    CLIENTS=0
    if wg show wg0 >/dev/null 2>&1; then
        WG_STATUS="up"
        if wg show wg0 | grep -q "latest handshake"; then
            WG_STATUS="connected"
        fi
    fi
    CLIENTS=$(iw dev wlan0 station dump 2>/dev/null | grep -c "Station" || echo 0)
    cat > "$STATUS_FILE" << SEOF
{
    "tunnel": "$WG_STATUS",
    "clients": $CLIENTS,
    "timestamp": "$(date -Iseconds)"
}
SEOF
    sleep 10
done
