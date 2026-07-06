# Deploying FaxxMe as a service

Runs FaxxMe as a **systemd** service (`python -m faxxme` under uvicorn), sets up
printer permissions, and streams logs to the journal.

## Install

```bash
sudo deploy/install.sh
```

Needs Python 3.10+. On Debian/Ubuntu the script auto-installs `python3-venv` + `python3-pip`
if they're missing (via `apt`); on other distros install those yourself first.

This will:
1. Create the virtualenv (`.venv`) and install `requirements.txt`.
2. Install a udev rule so USB thermal printers (`/dev/usb/lp*`) are writable by
   the `lp` group, and add your user to that group.
3. Copy `faxxme.env.example` → `faxxme.env` (your editable config).
4. Render and install `/etc/systemd/system/faxxme.service`.
5. Enable it (starts on boot) and start it now.

Re-run any time after pulling new code or changing config — it's idempotent.

## Manage the service

```bash
sudo systemctl start faxxme        # start
sudo systemctl stop faxxme         # stop
sudo systemctl restart faxxme      # restart (after code/config changes)
systemctl status faxxme            # is it running?
sudo systemctl disable faxxme      # don't start on boot
sudo systemctl enable faxxme       # start on boot
```

## Logs

```bash
journalctl -u faxxme -f            # follow live
journalctl -u faxxme -n 100        # last 100 lines
journalctl -u faxxme --since "10 min ago"
```

## Configure

Edit `deploy/faxxme.env`, then restart:

```bash
sudoedit deploy/faxxme.env
sudo systemctl restart faxxme
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `FAXXME_HOST` / `FAXXME_PORT` | `0.0.0.0` / `8000` | bind address |
| `FAXXME_LOG_LEVEL` | `info` | uvicorn log level |
| `FAXXME_LOCAL_USER` | `pi` | callsign whose faxes print on this host's printer |
| `FAXXME_PRINTER_DEV` | `/dev/usb/lp0` | printer device node |
| `FAXXME_PRINTER_POLL` | `4` | seconds between printer hot-replug checks |
| `FAXXME_WIDTH` / `FAXXME_PRINT_DOTS` | `32` / `384` | receipt width (58mm) |
| `FAXXME_CUT` | `full` | end-of-fax cut: `full` / `feed` (feed-to-cutter) / `partial` / `none` |

## Uninstall

```bash
sudo deploy/uninstall.sh           # removes service + udev rule; keeps code/db/venv
```

## Notes

- `deploy/faxxme.env` is git-ignored (host-specific). `faxxme.env.example` is the template.
- Logs go to the journal because uvicorn writes to stdout/stderr.
- For remote browsers to use WebUSB you still need HTTPS (e.g. `tailscale serve`);
  the local-bridge printer works over plain HTTP.
