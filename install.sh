#!/usr/bin/env bash
# One-shot setup for the attendance kiosk on Raspberry Pi OS.
# Idempotent — safe to re-run after every `git pull`.
#
# Options (env vars):
#   HOTSPOT_SSID=AttendancePi   Wi-Fi hotspot name
#   HOTSPOT_PASS=attendance123  Wi-Fi hotspot password (min 8 chars)
#   SKIP_HOTSPOT=1              don't touch Wi-Fi (e.g. installing over SSH/Wi-Fi)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="$(id -un)"
HOTSPOT_SSID="${HOTSPOT_SSID:-AttendancePi}"
HOTSPOT_PASS="${HOTSPOT_PASS:-attendance123}"

if [ "$(uname -s)" != "Linux" ]; then
  echo "install.sh is for the Raspberry Pi."
  echo "For development on this machine run:  python attendance_kiosk.py --mock"
  exit 1
fi

echo "==> [1/7] apt packages"
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential python3-venv curl

echo "==> [2/7] building readasync (Mercury API, UHF serial-only)"
make -C "$APP_DIR/c/src/api" TMR_ENABLE_UHF=1 TMR_ENABLE_SERIAL_READER_ONLY=1 readasync

echo "==> [3/7] python virtualenv"
if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> [4/7] serial port (udev rule + dialout group)"
sudo install -m 0644 "$APP_DIR/deploy/99-hecto.rules" /etc/udev/rules.d/99-hecto.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
NEED_RELOGIN=0
if ! id -nG "$APP_USER" | grep -qw dialout; then
  sudo usermod -aG dialout "$APP_USER"
  NEED_RELOGIN=1
fi

echo "==> [5/7] Wi-Fi hotspot ($HOTSPOT_SSID)"
if [ "${SKIP_HOTSPOT:-0}" = "1" ]; then
  echo "  skipped (SKIP_HOTSPOT=1)"
elif command -v nmcli >/dev/null 2>&1; then
  if nmcli -t -f NAME connection show | grep -qx "attendance-hotspot"; then
    echo "  hotspot connection already exists"
  else
    echo "  NOTE: this switches wlan0 to hotspot mode (drops current Wi-Fi/SSH-over-Wi-Fi)."
    if sudo nmcli device wifi hotspot ifname wlan0 con-name attendance-hotspot \
         ssid "$HOTSPOT_SSID" password "$HOTSPOT_PASS"; then
      sudo nmcli connection modify attendance-hotspot \
        connection.autoconnect yes connection.autoconnect-priority 100
    else
      echo "  hotspot creation failed — set it up later (see KIOSK_SETUP.md)"
    fi
  fi
else
  echo "  nmcli not found (older OS?) — see KIOSK_SETUP.md for manual hotspot setup"
fi

echo "==> [6/7] desktop icon"
chmod +x "$APP_DIR/launch-kiosk.sh" "$APP_DIR/enable-autostart.sh" \
  "$APP_DIR/run-attendance-kiosk.sh" "$APP_DIR/run-hecto-live.sh" 2>/dev/null || true
mkdir -p "$HOME/.local/share/applications"
sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/deploy/attendance-kiosk.desktop.in" \
  > "$HOME/.local/share/applications/attendance-kiosk.desktop"
if [ -d "$HOME/Desktop" ]; then
  cp "$HOME/.local/share/applications/attendance-kiosk.desktop" \
     "$HOME/Desktop/attendance-kiosk.desktop"
  chmod +x "$HOME/Desktop/attendance-kiosk.desktop"
  # mark trusted so PCManFM launches it without an "untrusted" prompt
  gio set "$HOME/Desktop/attendance-kiosk.desktop" metadata::trusted true 2>/dev/null || true
fi

echo "==> [7/7] disable screen blanking"
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_blanking 1 || true
fi

echo ""
echo "Done."
echo "  1. Plug in the M7E Hecto over USB (then check:  ls -l /dev/hecto )"
echo "  2. Double-click 'Attendance Kiosk' on the desktop to run the app."
echo "  3. Lecturer downloads (join Wi-Fi '$HOTSPOT_SSID', pass '$HOTSPOT_PASS'):"
echo "       http://10.42.0.1:8080/exports"
echo "  4. To boot straight into the kiosk later:  ./enable-autostart.sh"
if [ "$NEED_RELOGIN" = "1" ]; then
  echo "  IMPORTANT: log out and back in (or reboot) once so serial-port access applies."
fi
