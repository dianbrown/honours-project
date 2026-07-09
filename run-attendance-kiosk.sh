#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT_DIR/c/src/api"
READASYNC_BIN="$API_DIR/readasync"

URI="${ATTENDANCE_URI:-tmr:///dev/ttyUSB0}"
ANTENNA="${ATTENDANCE_ANTENNA:-1}"
READ_POWER="${ATTENDANCE_READ_POWER:-1900}"
PORT="${ATTENDANCE_PORT:-8080}"
HOST="${ATTENDANCE_HOST:-0.0.0.0}"

echo "[1/3] Ensuring readasync exists..."
if [[ ! -x "$READASYNC_BIN" || "$ROOT_DIR/c/src/samples/readasync.c" -nt "$READASYNC_BIN" ]]; then
  make -C "$API_DIR" TMR_ENABLE_UHF=1 TMR_ENABLE_SERIAL_READER_ONLY=1 readasync
fi

echo "[2/3] Ensuring Python dependencies..."
python3 -m pip install -r "$ROOT_DIR/requirements.txt"

echo "[3/3] Starting attendance kiosk server..."
exec python3 "$ROOT_DIR/attendance_kiosk.py" \
  --host "$HOST" \
  --port "$PORT" \
  --uri "$URI" \
  --antenna "$ANTENNA" \
  --read-power "$READ_POWER" \
  --readasync-bin "$READASYNC_BIN"
