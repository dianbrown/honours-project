#!/usr/bin/env bash
# Starts the attendance server (if not already running) and opens the kiosk UI
# full-screen in Chromium. This is what the "Attendance Kiosk" desktop icon runs.
set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${ATTENDANCE_PORT:-8080}"
URL="http://localhost:${PORT}"

cd "$APP_DIR"
mkdir -p data

if ! curl -fsS "$URL/api/state" >/dev/null 2>&1; then
  PYTHON="$APP_DIR/.venv/bin/python"
  [ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
  nohup "$PYTHON" "$APP_DIR/attendance_kiosk.py" >> "$APP_DIR/data/app.log" 2>&1 &
fi

# Wait for the server before opening the browser (avoids a white error page).
for _ in $(seq 1 30); do
  if curl -fsS "$URL/api/state" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

BROWSER="$(command -v chromium-browser || command -v chromium || true)"
if [ -z "$BROWSER" ]; then
  echo "Chromium not found — install it with: sudo apt install chromium-browser" >&2
  exit 1
fi

exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble --check-for-update-interval=31536000 \
  "$URL"
