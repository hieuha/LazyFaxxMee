# FAXXME

> Analog ghosts on a digital wire. Register, bind your printer to the browser, and fax
> your friends. If their printer is online it prints instantly; if not, it queues and
> prints the moment they plug in. No app to install — just a browser.

A terminal / CRT / hacker-vibe web app. Python backend (FastAPI + WebSocket), vanilla
JS frontend, physical printing over **WebUSB** (works with *any* printer the browser can
claim — thermal or otherwise) plus a server-side **local bridge** for a printer wired
directly into the host.

```
neo ──POST /api/fax──▶  FAXXME server  ──WebSocket push──▶  trinity's browser
                        (FastAPI, Pi)                        └─WebUSB─▶ 🖨 ESC/POS
                             │
                             └─ local bridge ─▶ /dev/usb/lp0  (host-attached printer)
```

## How it works

- **Presence = an open tab with a bound printer.** When you open the console and bind a
  printer (or the host has a local bridge), you are *online* and reachable.
- **Send:** `POST /api/fax`. If the recipient is online, the server pushes the fax over
  their WebSocket; their browser writes the ESC/POS bytes straight to the USB printer and
  acks. If they're offline, the fax is **queued** in SQLite.
- **Reconnect:** when they next open the console, the server flushes every queued fax.
- **ESC/POS is built server-side** (`faxxme/printer.py`) so there's one source of truth;
  the browser just forwards the raw bytes to the printer over WebUSB.
- **Image attachments** (optional) are Floyd–Steinberg dithered to 1-bit halftone
  (`faxxme/imaging.py`, Pillow), stored as a compact PNG, and printed as a `GS v 0` raster
  below the text. The compose form shows a live client-side halftone preview before sending.
  Config: `FAXXME_PRINT_DOTS` (width, default 384 ≈ 58mm), `FAXXME_IMG_MAX_H`, `FAXXME_MAX_UPLOAD`.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# listen on all interfaces; make the host's wired printer belong to callsign "pi"
FAXXME_LOCAL_USER=pi FAXXME_PRINTER_DEV=/dev/usb/lp0 ./run.sh
# -> http://<host>:8000
```

Environment variables:

| var | default | meaning |
|-----|---------|---------|
| `FAXXME_HOST` / `FAXXME_PORT` | `0.0.0.0` / `8000` | bind address |
| `FAXXME_LOCAL_USER` | *(unset)* | callsign whose faxes print on THIS host's printer |
| `FAXXME_PRINTER_DEV` | `/dev/usb/lp0` | device node for the local bridge |
| `FAXXME_WIDTH` | `32` | printer columns (58mm ≈ 32, 80mm ≈ 48) |
| `FAXXME_DB` / `FAXXME_SECRET` | in repo | sqlite + session-secret paths |

## ⚠️ WebUSB needs a secure context

Browsers only expose `navigator.usb` on **HTTPS or `localhost`**. So:

- Opening `http://localhost:8000` **on the Pi itself** → WebUSB works.
- Opening `http://192.168.10.82:8000` from another machine → WebUSB is **blocked**. Fixes:
  1. **Tailscale HTTPS (recommended)** — gives every device a real cert. Run on the host:
     ```bash
     sudo tailscale serve --bg http://localhost:8000
     # then browse https://groundstation-09.tail980c2.ts.net
     ```
     (Undo with `sudo tailscale serve reset`.)
  2. **Chrome flag** for LAN testing: `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
     → add `http://192.168.10.82:8000`.
  3. **Local bridge** — the host-attached printer prints server-side and needs no WebUSB
     at all. That's why the Pi's printer "just works" for callsign `pi`.

On **Linux clients**, the kernel `usblp` driver may hold the printer so the browser can't
claim it: `sudo modprobe -r usblp` first (or add a udev rule).

## Printer permissions (host)

The local bridge writes to `/dev/usb/lp0`, owned `root:lp`. This repo's setup added a udev
rule (`/etc/udev/rules.d/99-faxxme-printer.rules`, `MODE=0666`) and put `pi` in the `lp`
group so the server can print without root.

## Run with Docker

```bash
docker compose up -d --build      # build + start on http://<host>:8000
docker compose logs -f            # logs
docker compose down               # stop
```

The sqlite db + session secret persist in the `faxxme-data` volume.

**Printing:** browser/WebUSB printing works out of the box (it's client-side). To also
print on the *container host's* wired thermal printer (the "local bridge"), edit
`docker-compose.yml`: set `FAXXME_LOCAL_USER` to a callsign and uncomment the `devices` +
`group_add` block (the printer must be plugged in when the container starts). USB hotplug
is awkward in containers — for a host-attached printer the systemd deploy below is smoother.

## Run as a service (systemd)

Deploy on a host so it starts on boot and you can manage it with `systemctl`:

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

Covers auth + validation, offline queue → WebSocket flush → ack, the local-bridge
immediate print, send errors, and anonymous-WebSocket rejection.

## Layout

```
faxxme/app.py       FastAPI app: auth, fax routing, presence, WebSocket delivery
faxxme/db.py        SQLite (stdlib) — users + faxes (+ dithered image BLOB)
faxxme/auth.py      pbkdf2 password hashing + hmac-signed session cookies (no native deps)
faxxme/printer.py   ESC/POS receipt builder + local /dev printer bridge
faxxme/imaging.py   image -> Floyd–Steinberg halftone -> GS v 0 raster (Pillow)
static/             CRT terminal UI (index.html, style.css, app.js — WebUSB + WebSocket)
tests/test_api.py   end-to-end tests
```
