#!/bin/bash
# Disable screen blanking and power management
xset s off
xset -dpms
xset s noblank

# Hide cursor when idle
unclutter -idle 0.5 -root &

# Start window manager
openbox-session &

# Wait for portal to be ready
for i in $(seq 1 30); do
    curl -sf http://localhost:8080/ >/dev/null && break
    sleep 1
done

# Launch Chromium in kiosk mode
exec chromium-browser \
    --noerrdialogs \
    --disable-infobars \
    --disable-features=TranslateUI \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --kiosk \
    --app=http://localhost:8080/
