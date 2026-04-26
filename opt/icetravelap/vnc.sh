#!/bin/bash
# Wait for X server
for i in {1..30}; do
    AUTH=$(ls -t /tmp/serverauth.* 2>/dev/null | head -1)
    if [ -n "$AUTH" ] && pgrep -f "Xorg :0" >/dev/null; then
        break
    fi
    sleep 2
done
exec /usr/bin/x11vnc -noshm -display :0 -auth "$AUTH" -forever -nopw -shared -rfbport 5900 -quiet
