#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT_DIR/c/src/api"

URI=""
ANT="1"

usage() {
  cat <<'EOF'
Usage:
  ./run-hecto-live.sh [--uri <reader-uri-or-serial-dev>] [--ant <antenna>]

Examples:
  ./run-hecto-live.sh --uri /dev/ttyUSB0 --ant 1
  ./run-hecto-live.sh --uri tmr:///dev/ttyUSB0 --ant 1
  ./run-hecto-live.sh   # auto-detect /dev/ttyUSB0 or /dev/ttyACM0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uri)
      URI="${2:-}"
      shift 2
      ;;
    --ant)
      ANT="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$URI" ]]; then
  for dev in /dev/ttyUSB0 /dev/ttyACM0 /dev/ttyUSB1 /dev/ttyACM1; do
    if [[ -e "$dev" ]]; then
      URI="$dev"
      break
    fi
  done
fi

if [[ -z "$URI" ]]; then
  echo "No serial device auto-detected. Pass --uri /dev/ttyUSB0 (or your port)." >&2
  exit 1
fi

if [[ "$URI" != tmr://* && "$URI" != llrp://* ]]; then
  URI="tmr://$URI"
fi

echo "[1/2] Building readasync (UHF + serial-only)..."
make -C "$API_DIR" TMR_ENABLE_UHF=1 TMR_ENABLE_SERIAL_READER_ONLY=1 readasync

echo "[2/2] Starting live reads on $URI, antenna $ANT"
echo "Press Ctrl+C to stop."
exec "$API_DIR/readasync" "$URI" --ant "$ANT"
