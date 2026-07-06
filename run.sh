#!/usr/bin/env bash
# Launch the FaxxMe server, listening on all interfaces.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${FAXXME_HOST:-0.0.0.0}"
PORT="${FAXXME_PORT:-8000}"

# The username whose faxes print on THIS host's wired-in printer (local bridge).
# Leave unset to disable server-side printing (pure browser/WebUSB mode).
export FAXXME_LOCAL_USER="${FAXXME_LOCAL_USER:-}"
export FAXXME_PRINTER_DEV="${FAXXME_PRINTER_DEV:-/dev/usb/lp0}"

exec .venv/bin/uvicorn faxxme.app:app --host "$HOST" --port "$PORT" "$@"
