#!/usr/bin/env bash
# Install the FaxxMe printer agent as a systemd service on this Raspberry Pi.
#   sudo agent/install.sh
# Idempotent: re-run after editing config or pulling updates.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "please run with sudo:  sudo $0" >&2
  exit 1
fi

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
VENV="$AGENT_DIR/.venv"

echo "[agent] dir : $AGENT_DIR"
echo "[agent] user: $RUN_USER"

# 1) venv + websockets --------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "[agent] creating virtualenv…"
  sudo -u "$RUN_USER" python3 -m venv "$VENV"
fi
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet -r "$AGENT_DIR/requirements.txt"

# 2) printer permissions: udev rule + lp group --------------------------------
echo "[agent] installing printer udev rule + granting '$RUN_USER' the lp group…"
cat > /etc/udev/rules.d/99-faxxme-printer.rules <<'RULES'
KERNEL=="lp[0-9]*", SUBSYSTEM=="usbmisc", GROUP="lp", MODE="0666"
ACTION=="add", SUBSYSTEM=="usbmisc", KERNEL=="lp*", GROUP="lp", MODE="0666"
RULES
udevadm control --reload-rules
udevadm trigger --subsystem-match=usbmisc || true
usermod -aG lp "$RUN_USER"
for dev in /dev/usb/lp*; do [ -e "$dev" ] && chmod 666 "$dev" || true; done

# 3) config (never clobber an existing one) -----------------------------------
if [ ! -f "$AGENT_DIR/faxxme-agent.env" ]; then
  echo "[agent] creating faxxme-agent.env from example — EDIT IT with your token!"
  install -m 600 -o "$RUN_USER" "$AGENT_DIR/faxxme-agent.env.example" "$AGENT_DIR/faxxme-agent.env"
fi

# 4) systemd unit -------------------------------------------------------------
echo "[agent] installing systemd unit…"
sed -e "s|@AGENT_DIR@|$AGENT_DIR|g" -e "s|@RUN_USER@|$RUN_USER|g" \
    "$AGENT_DIR/faxxme-agent.service.template" > /etc/systemd/system/faxxme-agent.service

systemctl daemon-reload
systemctl enable faxxme-agent.service >/dev/null 2>&1 || true
systemctl restart faxxme-agent.service
sleep 1

echo
systemctl --no-pager --full status faxxme-agent.service | sed -n '1,8p' || true
echo
echo "[agent] installed. Next:"
echo "  1) sudoedit $AGENT_DIR/faxxme-agent.env    # set SERVER, callsign, TOKEN"
echo "  2) sudo systemctl restart faxxme-agent"
echo "  journalctl -u faxxme-agent -f              # live logs"
