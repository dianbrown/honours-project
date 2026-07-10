#!/usr/bin/env bash
# Toggle boot-to-kiosk mode (Stage B).
#   ./enable-autostart.sh        boot straight into the kiosk UI
#   ./enable-autostart.sh --off  back to the click-to-run desktop icon
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="$(id -un)"
SERVICE=/etc/systemd/system/attendance-kiosk.service
XDG_AUTOSTART="$HOME/.config/autostart/attendance-kiosk-browser.desktop"
LABWC_AUTOSTART="$HOME/.config/labwc/autostart"
WAYFIRE_INI="$HOME/.config/wayfire.ini"

if [ "${1:-}" = "--off" ]; then
  sudo systemctl disable --now attendance-kiosk.service 2>/dev/null || true
  sudo rm -f "$SERVICE"
  sudo systemctl daemon-reload
  rm -f "$XDG_AUTOSTART"
  [ -f "$LABWC_AUTOSTART" ] && sed -i '\|launch-kiosk.sh|d' "$LABWC_AUTOSTART"
  [ -f "$WAYFIRE_INI" ] && sed -i '\|launch-kiosk.sh|d' "$WAYFIRE_INI"
  echo "Boot-to-kiosk disabled. The desktop icon still works."
  exit 0
fi

# 1) server as a systemd service (starts on boot, restarts on crash)
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@APP_USER@|$APP_USER|g" \
  "$APP_DIR/deploy/attendance-kiosk.service.in" | sudo tee "$SERVICE" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now attendance-kiosk.service

# 2) browser autostart — cover the desktop sessions Pi OS ships:
#    XDG autostart (X11/LXDE), labwc (Bookworm Wayland), wayfire (early Bookworm)
mkdir -p "$(dirname "$XDG_AUTOSTART")"
cat > "$XDG_AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Attendance Kiosk Browser
Exec=$APP_DIR/launch-kiosk.sh
X-GNOME-Autostart-enabled=true
EOF

if [ -d "$HOME/.config/labwc" ] || command -v labwc >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/labwc"
  touch "$LABWC_AUTOSTART"
  grep -q "launch-kiosk.sh" "$LABWC_AUTOSTART" || \
    echo "$APP_DIR/launch-kiosk.sh &" >> "$LABWC_AUTOSTART"
fi

if [ -f "$WAYFIRE_INI" ] && ! grep -q "launch-kiosk.sh" "$WAYFIRE_INI"; then
  printf '\n[autostart]\nattendance_kiosk = %s/launch-kiosk.sh\n' "$APP_DIR" >> "$WAYFIRE_INI"
fi

echo "Boot-to-kiosk enabled. Test it with:  sudo reboot"
echo "Disable again with:  ./enable-autostart.sh --off"
