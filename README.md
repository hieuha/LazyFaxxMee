# FAXXME

> 🌐 Language: **English** · [Tiếng Việt](README-vi.md)

> Analog ghosts on a digital wire. Register, connect a printer, and fax your friends.
> If their printer is online it prints instantly; if not, it queues and prints the moment
> the printer comes back. No app to install — just a browser.

A terminal / CRT / hacker-vibe web app. Python backend (FastAPI + WebSocket), vanilla-JS
frontend. A fax prints on the recipient's physical thermal printer via any of **three paths** —
their **browser** (WebUSB), a printer wired into the **server host** (local bridge), or a headless
**agent on their own Raspberry Pi** (printer node, authenticated with a device token).

```mermaid
flowchart LR
    S["sender<br/>POST /api/fax"] --> F(["FAXXME server<br/>FastAPI"])
    F -- "WebSocket push" --> B["recipient's browser<br/>WebUSB → printer"]
    F -- "host local bridge" --> H["host printer<br/>/dev/usb/lp0"]
    F -- "their Pi agent · device token" --> A["Pi node printer<br/>/dev/usb/lp0"]
    F -. "offline" .-> Q[("SQLite queue")]
    Q -. "flush on reconnect / hot-replug" .-> F
```

## Screenshots

| The console | Recipient search | Printed-receipt view |
| :---: | :---: | :---: |
| ![FaxxMe console — status bar, compose, inbox/outbox](docs/screenshots/01-console.webp) | ![Type to search operators; online first](docs/screenshots/02-recipient-search.webp) | ![Any fax rendered as a torn paper slip](docs/screenshots/03-receipt.webp) |
| status bar · compose · inbox/outbox | fuzzy find, online-first | click a fax → paper slip |

## Features

- **Accounts** — register/login, pbkdf2 password hashing + hmac-signed session cookies (no native deps).
- **Compose** — searchable recipient picker, 200-char message, optional image, live char counter.
- **Three print paths**
  - *Browser WebUSB* — server builds the ESC/POS bytes, the recipient's browser forwards them raw to the USB printer (auto re-binds on hot-replug, no click).
  - *Local bridge* — a printer wired into the server host prints server-side, no browser needed.
  - *Printer node (agent)* — a headless [agent](agent/README.md) on the recipient's own Raspberry Pi, authenticated with a **device token**, prints faxes locally.
- **Device tokens** — per-account API token for the agent (sha256-hashed, shown once); regenerate to **revoke instantly** (the connected agent is kicked).
- **Live printer status** — the PRINTER pill shows the best available path (`ONLINE` browser USB · `NODE ✓` agent · `WIRED` bridge · `OFFLINE`) and updates in real time; **TEST** prints a test page on whichever you have.
- **Unicode text** — lines with Vietnamese, emoji, or anything the printer's code page can't show are auto-rendered with a bundled font as a crisp `GS v 0` raster; pure-ASCII lines stay fast native ESC/POS text.
- **Image attachments** — Floyd–Steinberg dithered to 1-bit halftone (`GS v 0` raster), with a live client-side preview.
- **Offline queue** — undelivered faxes wait in SQLite and flush when the recipient (browser/agent) reconnects, or when the host printer is hot-replugged (background watcher); the sender's outbox flips `queued → printed` live.
- **Printed-receipt modal** — click any fax to see it as a paper slip (torn edges, dithered image).
- **Housekeeping** — clear inbox/outbox (only your side; the other party keeps their copy), auto-cap at 50 per side, can't fax yourself.
- **Configurable auto-cut** — full / feed-to-cutter / partial / none.
- **`/healthz`** — liveness probe for Docker / systemd / uptime checks.

## How it works

- **Presence = a live WebSocket.** With the console tab open you're *online*: faxes are pushed
  to you instantly and friends see your green dot.
- **Send** (`POST /api/fax`). If the recipient is online, the server pushes the fax over their
  WebSocket; their browser writes the ESC/POS bytes to the USB printer and acks. If they're
  offline, the fax is **queued** in SQLite.
- **Delivery on return.** Queued faxes flush when the recipient reconnects (browser **or** Pi
  agent), or — for the host's wired printer — when the background watcher sees the device
  reappear (polls every `FAXXME_PRINTER_POLL` seconds; covers unplug/replug without a restart).
- **Printer node = another WebSocket client.** The agent authenticates with a device token,
  connects the same `/ws`, and writes the pushed ESC/POS bytes to its local printer — so
  "others print to me" works with zero extra server logic (presence, queue, acks all reused).
- **Single source of truth.** ESC/POS is built server-side (`faxxme/printer.py`); the browser
  forwards raw bytes over WebUSB, and the local bridge / agent write the same bytes to `/dev`.

## Documentation

Deep-dive docs live in [`docs/`](docs/):

- [How it works](docs/how-it-works.md) — architecture, delivery model, watcher, imaging, tokens, admin.
- [Printer compatibility](docs/printers.md) — supported thermal printers, widths, auto-cut.
- [Platform notes](docs/platforms.md) — WebUSB gotchas on Ubuntu / macOS / Windows.
- [Webhook integration](docs/webhook.md) — let any site fax you (blog comments, apps): inbound API, samples, security, secret-key management.
- [Printer node / agent](agent/README.md) — run FaxxMe on your own Pi (device token, no browser).

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
| `FAXXME_FAX_RATE_MAX` / `FAXXME_FAX_RATE_WINDOW` | `20` / `60` | per-sender rate limit: max faxes per N seconds (0 = off) |
| `FAXXME_WEBHOOK_RATE_MAX` / `FAXXME_WEBHOOK_RATE_WINDOW` | `5` / `300` | inbound webhook rate limit, enforced per author **and** per calling-site IP (0 = off) |
| `FAXXME_WEBHOOK_MSG_MAX` | `500` | max characters in an inbound webhook message |
| `FAXXME_ADMIN_PASSWORD_HASH` | *(unset)* | sha256 hash of the `/admin` password; unset = admin disabled |
| `FAXXME_FONT` | bundled Play (Google Fonts) | TTF used to render non-ASCII text (Vietnamese, emoji…) |
| `FAXXME_FONT_SIZE` | `26` | font size for rendered Unicode text |
| `FAXXME_FONT_THRESHOLD` | `176` | black/white cutoff for rendered text (higher = darker) |
| `FAXXME_FOOTER_FONT_SIZE` | `22` | font size for the small webhook attribution footer (name/post/url) |
| `FAXXME_DB` / `FAXXME_SECRET` | in repo | sqlite + session-secret paths |

## API

| method | path | purpose |
|--------|------|---------|
| POST | `/api/register` · `/api/login` · `/api/logout` | auth (form fields) |
| GET | `/api/me` | current user + `printer_online`, `local_bridge`, `node_online`, `has_token`, `webhook_secret` |
| GET | `/api/users` | other operators + online flags |
| POST | `/api/fax` | send (multipart: `to`, `body`, optional `image`) |
| GET | `/api/inbox` · `/api/outbox` | fax history (newest 50) |
| POST | `/api/inbox/clear` · `/api/outbox/clear` | clear your side |
| GET | `/api/fax/{id}/image` | the dithered PNG (sender/recipient only) |
| POST | `/api/token/regenerate` | issue a device token (shown once); revokes + kicks the old one |
| POST | `/api/webhook/regenerate` · `/api/webhook/revoke` | mint / revoke a **webhook secret key** (re-viewable in the panel) — see [Webhook integration](#webhook-integration) |
| POST | `/api/fax/inbound` | **public webhook** — any site posts a message that prints as a fax (auth via secret key, not a session) |
| POST | `/api/test-print` | print a test page on your node/bridge |
| POST | `/api/admin/login` · `/api/admin/logout` | admin session (password → signed cookie; separate from user auth) |
| GET | `/admin` · `/api/admin/*` | admin panel: paginated users + faxes, delete, revoke tokens, stats (admin cookie only) |
| WS | `/ws` | presence + live delivery + status/node pushes; auth via **session cookie** (browser) or **`Authorization: Bearer <token>` + `X-Faxxme-User`** (agent) |
| GET | `/healthz` | `{status, printer_bridge}` |
| GET | `/` | the single-page CRT console |

## Webhook integration

Let any external site fax **you** — for example straight from a blog's comment box (e.g. [lazyblog](https://github.com/hieuha/lazyblog)). The end sender doesn't need a FaxxMe account — the site authenticates on their behalf with your **secret key**. It's a plain webhook: anyone holding the secret can POST a message that prints on your printer.

> 📖 **Full guide:** [docs/webhook.md](docs/webhook.md) — sample requests (PHP/Python/Node), security, secret-key management, administration, and troubleshooting.

**How it fits together**

```mermaid
flowchart LR
    R["visitor<br/>comment box"] -->|"POST (same-origin)"| B["your site server<br/>(e.g. blog plugin)"]
    B -->|"POST /api/fax/inbound<br/>Authorization: Bearer fxwh_…"| F(["FAXXME server"])
    F --> P["your printer"]
```

The site calls FaxxMe **server-side**, not from the visitor's browser. That keeps the secret key hidden, needs no CORS, and lets the site add its own per-visitor checks (captcha, its own rate limit) before forwarding.

**Set up (author):** log in → `:: WEBHOOK INTEGRATION → GENERATE SECRET KEY`. The key (`fxwh_…`) shows masked — click the **eye** to reveal, **copy** to copy (it stays viewable in the panel). Hand it to whoever runs the site, to store **server-side** (e.g. in the site's `.env`). The `↻` icon rotates it (old key dies instantly); `revoke` turns the webhook off entirely.

**Scope & safety:** a secret key can *only* deliver a fax to the author who owns it — there's no recipient field to target anyone else. Inbound faxes are rate-limited per author **and** per calling-site IP (derived server-side, not spoofable; `FAXXME_WEBHOOK_RATE_MAX`/`FAXXME_WEBHOOK_RATE_WINDOW`), messages are capped at `FAXXME_WEBHOOK_MSG_MAX`, and they print immediately (fire-and-forget) attributed to the reserved `@webhook` sender. Being spammed? Revoke the key.

**`POST /api/fax/inbound`** — `Content-Type: application/x-www-form-urlencoded`, header `Authorization: Bearer <secret key>`:

| field | required | notes |
|-------|----------|-------|
| `body` | ✅ | the message (≤ `FAXXME_WEBHOOK_MSG_MAX` chars) |
| `name` | – | sender's name (≤ 40) — printed as attribution |
| `post` | – | source title, e.g. a post title (≤ 120) |
| `url` | – | source URL (≤ 200) |

FaxxMe derives the client IP itself for per-IP rate limiting (no spoofable IP field); that IP is your calling server, so add your own per-visitor throttle. Full details in [docs/webhook.md](docs/webhook.md).

Returns `{ "ok": true, "fax_id": …, "delivered": bool }`. Errors: `401` (missing/invalid secret), `400` (empty/too-long message), `429` (rate-limited).

**Caller side (PHP snippet, e.g. for a blog plugin):**

```php
<?php
// Forward a comment to FaxxMe as a fax. Runs server-side; the secret never reaches the browser.
$faxxme = 'https://fax.hatrunghieu.com';
$secret = getenv('FAXXME_SECRET_KEY');   // fxwh_… , kept out of version control

$ch = curl_init("$faxxme/api/fax/inbound");
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => ["Authorization: Bearer $secret"],
    CURLOPT_POSTFIELDS     => http_build_query([
        'body'      => $_POST['message'] ?? '',
        'name'      => $_POST['name'] ?? '',
        'post'      => $postTitle,
        'url'       => $postUrl,
    ]),
    CURLOPT_TIMEOUT        => 10,
]);
$res  = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);   // 200 ok · 429 too fast · 401 bad secret
curl_close($ch);
```

Curl equivalent for a quick test:

```bash
curl -X POST https://fax.hatrunghieu.com/api/fax/inbound \
  -H "Authorization: Bearer fxwh_XXXX" \
  --data-urlencode "body=great post!" \
  --data-urlencode "name=A reader" \
  --data-urlencode "post=My First Fax" \
  --data-urlencode "url=https://blog.example/first"
```

## ⚠️ WebUSB needs a secure context

Browsers only expose `navigator.usb` on **HTTPS or `localhost`**, and only Chromium-based
browsers support it at all (no Safari/Firefox). Also, on **macOS/Windows** the OS claims
class-compliant USB printers, so they won't appear in the WebUSB picker. Options:

1. **Local bridge / printer node (simplest)** — a printer wired into the server host, or the
   recipient's own Pi running the [agent](agent/README.md), prints server-side with no WebUSB
   at all. That's why the Pi's printer "just works".
2. **Tailscale HTTPS** — real certs for the whole tailnet:
   ```bash
   sudo tailscale serve --bg http://localhost:8000   # → https://<host>.<tailnet>.ts.net
   ```
   (undo: `sudo tailscale serve reset`)
3. **Chrome flag** for LAN testing: `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.

On **Linux clients** the kernel `usblp` driver may hold the printer: `sudo modprobe -r usblp`
first (this disables the host's own local bridge though). Full per-OS guidance:
[docs/platforms.md](docs/platforms.md).

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

## Printer node (Raspberry Pi agent)

Can't bind a printer through the browser (macOS/Windows) — or just want a dedicated,
always-on printer? Run the **agent** on a Raspberry Pi with the printer attached. It signs
in with your callsign + a **device token** (web UI: `:: PRINTER NODE → GENERATE TOKEN`,
regenerate to revoke) and prints every fax addressed to you — no browser needed.

```bash
sudo agent/install.sh
sudoedit agent/faxxme-agent.env    # set FAXXME_SERVER, callsign, token
sudo systemctl restart faxxme-agent
```

Full guide: [agent/README.md](agent/README.md).

## Admin panel

The `/admin` panel is **completely separate from user accounts** — it's gated by a single
password whose **sha256 hash** you put in `FAXXME_ADMIN_PASSWORD_HASH` (leave it unset to disable
`/admin` entirely). No admin user, no extra DB table. Generate the hash and run:

```bash
# sha256 of your chosen admin password
python3 -c "import hashlib;print(hashlib.sha256(b'my-admin-pass').hexdigest())"

FAXXME_ADMIN_PASSWORD_HASH=<that hash> ./run.sh
```

Then open **`/admin`**, unlock with the password (its own signed session cookie), and you get a
terminal-styled control room to:

- see live **stats** (operators, online now, transmissions, queued/delivered, images);
- browse **operators** (paginated, 20/page) with sent/received counts, the **last session**
  (IP + User-Agent, Cloudflare/proxy-aware, with a last-seen kept live by a heartbeat) +
  online/node/token status,
  **revoke a device token**, or **delete a user** (a *tombstone* — the account is anonymized and
  can't log in, but its faxes are kept for the other party and the callsign is freed);
- browse/search **all transmissions** (paginated, 20/page), **view** any one as a printed paper
  slip (full text + image), and **delete any fax** (both sides).

Confirmations and the message viewer reuse the console's own terminal-styled modal. The API under
`/api/admin/*` checks the admin cookie server-side (`401` without it), so the page itself is
harmless to serve.

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
control, per-side clear, the 50-message cap, self-fax rejection, message-length limit,
anonymous-WebSocket rejection, **device-token auth + revocation** (incl. bad-token reject),
the **node-online** indicator, **test-print** routing to the agent, **Unicode-body raster
rendering**, and the **per-sender rate limit**.

## Project layout

```
faxxme/__main__.py   `python -m faxxme` — the uvicorn daemon entrypoint
faxxme/app.py        FastAPI app: auth, fax routing, presence, WS delivery, printer watcher, tokens
faxxme/db.py         SQLite (stdlib) — users (+ device-token hash) + faxes (+ dithered image BLOB)
faxxme/auth.py       pbkdf2 passwords + hmac session cookies + device tokens (no native deps)
faxxme/printer.py    ESC/POS receipt builder + auto-cut + local /dev printer bridge
faxxme/imaging.py    image → halftone raster + Unicode text → crisp raster (Pillow)
faxxme/fonts/        bundled Play font (renders Vietnamese/emoji)
static/              CRT terminal UI (index.html, style.css, app.js — WebUSB + WebSocket)
agent/               printer-node agent for a Raspberry Pi (faxxme_agent.py, systemd, install)
tests/test_api.py    end-to-end tests
deploy/              systemd unit, udev rule, env, install/uninstall scripts
Dockerfile · docker-compose.yml
```
