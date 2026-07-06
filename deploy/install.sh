#!/usr/bin/env bash
# Install FaxxMe as a systemd service on this host.
#   sudo deploy/install.sh
# Idempotent: safe to re-run to pick up code/config/dependency changes.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "please run with sudo:  sudo $0" >&2
  exit 1
fi

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$DEPLOY_DIR")"
RUN_USER="${SUDO_USER:-$(id -un)}"
VENV="$PROJECT_DIR/.venv"

echo "[faxxme] project : $PROJECT_DIR"
echo "[faxxme] run user: $RUN_USER"

# 1) python venv + dependencies (as the run user, not root) -------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "[faxxme] creating virtualenv…"
  sudo -u "$RUN_USER" python3 -m venv "$VENV"
fi
echo "[faxxme] installing dependencies…"
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"

# 2) printer permissions: udev rule + lp group --------------------------------
echo "[faxxme] installing printer udev rule + granting '$RUN_USER' the lp group…"
install -m 644 "$DEPLOY_DIR/99-faxxme-printer.rules" /etc/udev/rules.d/99-faxxme-printer.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usbmisc || true
usermod -aG lp "$RUN_USER"
# make an already-plugged printer writable right now
for dev in /dev/usb/lp*; do [ -e "$dev" ] && chmod 666 "$dev" || true; done

# 3) config file (never clobber an existing one) ------------------------------
if [ ! -f "$DEPLOY_DIR/faxxme.env" ]; then
  echo "[faxxme] creating deploy/faxxme.env from example…"
  install -m 644 -o "$RUN_USER" "$DEPLOY_DIR/faxxme.env.example" "$DEPLOY_DIR/faxxme.env"
fi

# 4) render + install the systemd unit ----------------------------------------
echo "[faxxme] installing systemd unit…"
sed -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
    -e "s|@DEPLOY_DIR@|$DEPLOY_DIR|g" \
    -e "s|@RUN_USER@|$RUN_USER|g" \
    "$DEPLOY_DIR/faxxme.service.template" > /etc/systemd/system/faxxme.service

# 5) enable + (re)start -------------------------------------------------------
systemctl daemon-reload
systemctl enable faxxme.service >/dev/null 2>&1 || true
systemctl restart faxxme.service
sleep 1

echo
systemctl --no-pager --full status faxxme.service | sed -n '1,10p' || true
echo
echo "[faxxme] installed. Handy commands:"
echo "  sudo systemctl start|stop|restart faxxme"
echo "  systemctl status faxxme"
echo "  journalctl -u faxxme -f          # live logs"
echo "  sudoedit $DEPLOY_DIR/faxxme.env  # change port/printer, then: sudo systemctl restart faxxme"
