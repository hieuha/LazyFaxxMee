#!/usr/bin/env bash
# Remove the FaxxMe systemd service + udev rule.
# Leaves your code, virtualenv, database and config in place.
#   sudo deploy/uninstall.sh
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "please run with sudo:  sudo $0" >&2
  exit 1
fi

echo "[faxxme] stopping + disabling service…"
systemctl disable --now faxxme.service 2>/dev/null || true
rm -f /etc/systemd/system/faxxme.service
systemctl daemon-reload

echo "[faxxme] removing printer udev rule…"
rm -f /etc/udev/rules.d/99-faxxme-printer.rules
udevadm control --reload-rules || true

echo "[faxxme] done. (venv, database, config and code left untouched)"
