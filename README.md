# FAXXME

> Analog ghosts on a digital wire. Register, connect a printer, and fax your friends.
> If their printer is online it prints instantly; if not, it queues and prints the moment
> the printer comes back. No app to install — just a browser.

A terminal / CRT / hacker-vibe web app. Python backend (FastAPI + WebSocket), vanilla-JS
frontend, physical printing over **WebUSB** (any printer the browser can claim — thermal or
otherwise) plus a server-side **local bridge** for a printer wired directly into the host.

```
neo ──POST /api/fax──▶  FAXXME server  ──WebSocket push──▶  trinity's browser
                        (FastAPI)                           └─WebUSB─▶ 🖨 ESC/POS
                             │
                             └─ local bridge ─▶ /dev/usb/lp0  (host-attached printer)
                                 ▲ background watcher re-flushes the queue on hot-replug
```

## Screenshots

| The console | Recipient search | Printed-receipt view |
| :---: | :---: | :---: |
| ![FaxxMe console — status bar, compose, inbox/outbox](docs/screenshots/01-console.webp) | ![Type to search operators; online first](docs/screenshots/02-recipient-search.webp) | ![Any fax rendered as a torn paper slip](docs/screenshots/03-receipt.webp) |
| status bar · compose · inbox/outbox | fuzzy find, online-first | click a fax → paper slip |

## Features

- **Accounts** — register/login, pbkdf2 password hashing + hmac-signed session cookies (no native deps).
- **Compose** — searchable recipient picker, 200-char message, optional image, live char counter.
- **Two print paths**
  - *Browser WebUSB* — server builds the ESC/POS bytes, the recipient's browser forwards them raw to the USB printer.
  - *Local bridge* — a printer wired into the host prints server-side, no browser needed.
- **Image attachments** — Floyd–Steinberg dithered to 1-bit halftone (`GS v 0` raster), with a live client-side preview.
- **Offline queue** — undelivered faxes wait in SQLite and flush when the recipient (or the host printer) comes back.
- **Printer hotplug watcher** — auto-prints the queue when a wired printer reappears, and pushes a live status update so the sender's outbox flips `queued → printed` without a refresh.
- **Printed-receipt modal** — click any fax to see it as a paper slip (torn edges, dithered image).
- **Housekeeping** — clear inbox/outbox (only your side; senders/recipients keep their copy), auto-cap at 50 per side, can't fax yourself.
- **Configurable auto-cut** — full / feed-to-cutter / partial / none.
- **`/healthz`** — liveness probe for Docker / systemd / uptime checks.

## How it works

- **Presence = a live WebSocket.** With the console tab open you're *online*: faxes are pushed
  to you instantly and friends see your green dot.
- **Send** (`POST /api/fax`). If the recipient is online, the server pushes the fax over their
  WebSocket; their browser writes the ESC/POS bytes to the USB printer and acks. If they're
  offline, the fax is **queued** in SQLite.
- **Delivery on return.** Queued faxes flush when the recipient reconnects their browser, or —
  for the host's wired printer — when the background watcher sees the device reappear (polls
  every `FAXXME_PRINTER_POLL` seconds; covers unplug/replug without a restart).
- **Single source of truth.** ESC/POS is built server-side (`faxxme/printer.py`); the browser
  just forwards raw bytes over WebUSB, and the local bridge writes the same bytes to `/dev`.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# listen on all interfaces; make the host's wired printer belong to callsign "pi"
FAXXME_LOCAL_USER=pi FAXXME_PRINTER_DEV=/dev/usb/lp0 ./run.sh
# or: FAXXME_LOCAL_USER=pi .venv/bin/python -m faxxme
# -> http://<host>:8000
```

## Configuration

All configuration is via environment variables:

| var | default | meaning |
|-----|---------|---------|
| `FAXXME_HOST` / `FAXXME_PORT` | `0.0.0.0` / `8000` | bind address |
| `FAXXME_LOG_LEVEL` | `info` | uvicorn log level |
| `FAXXME_LOCAL_USER` | *(unset)* | callsign whose faxes print on THIS host's printer (enables the local bridge) |
| `FAXXME_PRINTER_DEV` | `/dev/usb/lp0` | printer device node for the local bridge |
| `FAXXME_PRINTER_POLL` | `4` | seconds between printer hot-replug checks |
| `FAXXME_CUT` | `full` | end-of-fax cut: `full` / `feed` (feed-to-cutter) / `partial` / `none` |
| `FAXXME_WIDTH` | `32` | text columns (58mm ≈ 32, 80mm ≈ 48) |
| `FAXXME_PRINT_DOTS` | `384` | image raster width in dots (58mm ≈ 384, 80mm ≈ 576) |
| `FAXXME_IMG_MAX_H` | `1200` | max printed image height (dots) |
| `FAXXME_MAX_UPLOAD` | `6291456` | max image upload size (bytes, 6 MB) |
| `FAXXME_DB` / `FAXXME_SECRET` | in repo | sqlite + session-secret paths |

## API

| method | path | purpose |
|--------|------|---------|
| POST | `/api/register` · `/api/login` · `/api/logout` | auth (form fields) |
| GET | `/api/me` | current user + printer/bridge status |
| GET | `/api/users` | other operators + online flags |
| POST | `/api/fax` | send (multipart: `to`, `body`, optional `image`) |
| GET | `/api/inbox` · `/api/outbox` | fax history (newest 50) |
| POST | `/api/inbox/clear` · `/api/outbox/clear` | clear your side |
| GET | `/api/fax/{id}/image` | the dithered PNG (sender/recipient only) |
| WS | `/ws` | presence + live delivery + status pushes |
| GET | `/healthz` | `{status, printer_bridge}` |
| GET | `/` | the single-page CRT console |

## ⚠️ WebUSB needs a secure context

Browsers only expose `navigator.usb` on **HTTPS or `localhost`**, and only Chromium-based
browsers support it at all (no Safari/Firefox). Also, on **macOS/Windows** the OS claims
class-compliant USB printers, so they won't appear in the WebUSB picker. Options:

1. **Local bridge (simplest)** — the host-attached printer prints server-side and needs no
   WebUSB. That's why the Pi's printer "just works" for callsign `pi`.
2. **Tailscale HTTPS** — real certs for the whole tailnet:
   ```bash
   sudo tailscale serve --bg http://localhost:8000   # → https://<host>.<tailnet>.ts.net
   ```
   (undo: `sudo tailscale serve reset`)
3. **Chrome flag** for LAN testing: `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.

On **Linux clients** the kernel `usblp` driver may hold the printer: `sudo modprobe -r usblp`
first (this disables the host's own local bridge though).

## Printer permissions (host)

The local bridge writes to `/dev/usb/lp*` (owned `root:lp`). `deploy/install.sh` installs a
udev rule (`/etc/udev/rules.d/99-faxxme-printer.rules`, group `lp`, mode `0666`) and adds the
run user to the `lp` group so the server can print without root — and so the device is writable
again automatically after a replug.

## Run with Docker

```bash
docker compose up -d --build      # build + start on http://<host>:8000
docker compose logs -f
docker compose down
```

DB + session secret persist in the `faxxme-data` volume. Browser/WebUSB printing works out of
the box; to print on the *container host's* wired printer, set `FAXXME_LOCAL_USER` and uncomment
the `devices` + `group_add` block in `docker-compose.yml`. USB hotplug is awkward in containers —
for a host-attached printer the systemd deploy is smoother.

## Run as a service (systemd)

```bash
sudo deploy/install.sh          # venv + deps, printer udev rule, systemd unit
systemctl status faxxme
journalctl -u faxxme -f          # logs
```

See [deploy/README.md](deploy/README.md) for stop/start/config/uninstall.

## Test

```bash
.venv/bin/python -m pytest tests/ -q
```

Covers `/healthz`, auth + validation, offline queue → WebSocket flush → ack, immediate
local-bridge print, **queue flush on printer reconnect**, image dithering + raster + access
control, per-side clear, the 50-message cap, self-fax rejection, message-length limit, and
anonymous-WebSocket rejection.

## Project layout

```
faxxme/__main__.py   `python -m faxxme` — the uvicorn daemon entrypoint
faxxme/app.py        FastAPI app: auth, fax routing, presence, WS delivery, printer watcher
faxxme/db.py         SQLite (stdlib) — users + faxes (+ dithered image BLOB)
faxxme/auth.py       pbkdf2 password hashing + hmac-signed session cookies (no native deps)
faxxme/printer.py    ESC/POS receipt builder + auto-cut + local /dev printer bridge
faxxme/imaging.py    image → Floyd–Steinberg halftone → GS v 0 raster (Pillow)
static/              CRT terminal UI (index.html, style.css, app.js — WebUSB + WebSocket)
tests/test_api.py    end-to-end tests
deploy/              systemd unit, udev rule, env, install/uninstall scripts
Dockerfile · docker-compose.yml
```
