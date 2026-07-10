#!/usr/bin/env bash
# Manual start of the kiosk server on the Pi (the desktop icon / launch-kiosk.sh
# is the normal way — this is for a terminal, or pass --mock for development).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT_DIR/c/src/api"
READASYNC_BIN="$API_DIR/readasync"

PORT="${ATTENDANCE_PORT:-8080}"
HOST="${ATTENDANCE_HOST:-0.0.0.0}"

PYTHON="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "[setup] creating virtualenv..."
  python3 -m venv "$ROOT_DIR/.venv"
  "$ROOT_DIR/.venv/bin/pip" install -r "$ROOT_DIR/requirements.txt"
fi

if [[ "$*" != *"--mock"* ]]; then
  if [[ ! -x "$READASYNC_BIN" || "$ROOT_DIR/c/src/samples/readasync.c" -nt "$READASYNC_BIN" ]]; then
    echo "[setup] building readasync..."
    make -C "$API_DIR" TMR_ENABLE_UHF=1 TMR_ENABLE_SERIAL_READER_ONLY=1 readasync
  fi
fi

exec "$PYTHON" "$ROOT_DIR/attendance_kiosk.py" \
  --host "$HOST" \
  --port "$PORT" \
  --readasync-bin "$READASYNC_BIN" \
  "$@"
