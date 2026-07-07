# Deploying FaxxMe as a service

> 🌐 Language: **English** · [Tiếng Việt](README-vi.md)

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
| `FAXXME_FAX_RATE_MAX` / `FAXXME_FAX_RATE_WINDOW` | `20` / `60` | anti-spam: max faxes per N seconds (0 = off) |
| `FAXXME_ADMIN_PASSWORD_HASH` | *(unset)* | sha256 of the `/admin` password; unset = admin disabled |

The main [README](../README.md#configuration) lists the full set (image caps, Unicode-font tuning, DB/secret paths).

### Enable the admin panel

The `/admin` panel is off until you set an admin password (as its **sha256 hash** — the plaintext
never touches the config). Generate the hash, put it in `deploy/faxxme.env`, and restart:

```bash
# 1) hash your chosen password (replace the text in b'...')
python3 -c "import hashlib;print(hashlib.sha256(b'your-admin-password').hexdigest())"

# 2) put the printed hash in deploy/faxxme.env:
#      FAXXME_ADMIN_PASSWORD_HASH=<paste the hash>
sudoedit deploy/faxxme.env

# 3) apply
sudo systemctl restart faxxme
```

Then open `http://<host>:8000/admin` and unlock with the password. Leave
`FAXXME_ADMIN_PASSWORD_HASH` blank to disable the panel again.

## Uninstall

```bash
sudo deploy/uninstall.sh           # removes service + udev rule; keeps code/db/venv
```

## Notes

- `deploy/faxxme.env` is git-ignored (host-specific). `faxxme.env.example` is the template.
- Logs go to the journal because uvicorn writes to stdout/stderr.
- For remote browsers to use WebUSB you still need HTTPS (e.g. `tailscale serve`);
  the local-bridge printer works over plain HTTP.
