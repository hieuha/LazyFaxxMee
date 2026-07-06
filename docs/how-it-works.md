# How FaxxMe works

FaxxMe simulates the old thrill of faxing a friend: you type a message (and maybe attach
an image), and it prints on **their** physical thermal printer — instantly if they're
around, or the moment their printer comes back if not.

```
  sender                          FAXXME server (FastAPI)             recipient prints via…
 ┌────────────┐  POST /api/fax  ┌────────────────────────┐  WS push  ┌────────────────────────┐
 │compose form│ ──────────────▶ │  deliver(fax):         │ ────────▶ │ browser (WebUSB)   🖨   │
 └────────────┘                 │   1. recipient online? │           │ or their Pi agent  🖨   │
     ▲ "printed" (WS)           │   2. host local bridge │           └────────────────────────┘
     └──────────────────────────│   3. else → queue (DB) │  …or host local bridge → /dev/usb/lp0
                                └───────────┬────────────┘  (background watcher flushes on replug)
                                   SQLite ◀─┘
```

## Components

| file | role |
|------|------|
| `faxxme/app.py` | FastAPI routes, WebSocket presence, delivery logic, printer watcher, device tokens |
| `faxxme/db.py` | SQLite (stdlib): `users` (+ device-token hash) + `faxes` (dithered-image BLOB, per-side delete flags) |
| `faxxme/auth.py` | pbkdf2 passwords + hmac session cookies + device tokens (no native deps) |
| `faxxme/printer.py` | builds the ESC/POS receipt, auto-cut, and the local `/dev` printer bridge |
| `faxxme/imaging.py` | image → Floyd–Steinberg 1-bit halftone → `GS v 0` raster (Pillow) |
| `static/` | the CRT single-page UI (WebUSB + WebSocket client) |
| `agent/` | the headless printer-node agent for a Raspberry Pi (see [../agent/README.md](../agent/README.md)) |

## Presence — who's "online"

A user is **online** exactly while they have a live WebSocket (`/ws`) — i.e. the console
tab is open. The server keeps an in-memory map `user_id → {sockets}`. Online users:

- receive faxes pushed in real time,
- show a green dot in everyone's recipient search,
- get live status updates (`queued → printed`).

Presence is **not** persisted; it's purely "is a socket connected right now". The server also
tracks, per user, whether any connected socket is an **agent** (`node_online`) so the web UI
can show `PRINTER: NODE ✓`.

## Sending a fax

`POST /api/fax` (multipart: `to`, `body`, optional `image`) runs `deliver(fax)`, which
tries three things in order:

1. **Recipient online (WebSocket)** → the server pushes the fax (with ready-to-print
   ESC/POS bytes, base64) over their socket. The client — a **browser** (WebUSB) *or* a
   **Pi agent** — writes the bytes to the printer and sends an **ack**; the server marks it
   `delivered`.
2. **Host local bridge** → if the recipient's callsign equals `FAXXME_LOCAL_USER` and the
   host's printer device is writable, the server prints the bytes itself to
   `FAXXME_PRINTER_DEV` (e.g. `/dev/usb/lp0`) and marks it `delivered`. No browser needed.
3. **Neither** → the fax stays `pending` in SQLite.

## Delivery on return

A queued fax leaves the queue when:

- **the recipient reconnects** (browser *or* Pi agent) — on WebSocket connect the server
  flushes every `pending` fax to them; or
- **the host printer reappears** — a background **watcher** polls `FAXXME_PRINTER_DEV`
  every `FAXXME_PRINTER_POLL` seconds (default 4). When the device is writable again
  (e.g. after an unplug/replug), it prints the local-bridge user's queued faxes and pushes
  a `status` message so the sender's outbox flips `queued → printed` with no refresh.

The watcher also runs once at startup, so a reboot flushes anything queued.

On the **browser/WebUSB** side there's a client-side equivalent: WebUSB permission persists,
so FaxxMe re-binds a previously-authorized printer automatically on page load, and listens
for USB `connect`/`disconnect` events — unplug/replug the printer and it re-binds and prints
the waiting faxes on its own, no *CONNECT PRINTER* click needed.

## Printer node (agent) & device tokens

Instead of a browser, a user can run a headless **agent** on their own Raspberry Pi. It's just
another WebSocket client, so the whole delivery model above works unchanged — the agent receives
the same `fax` messages and writes the ESC/POS bytes to its local printer.

- **Auth.** `/ws` accepts either a **session cookie** (browser) or a **device token** (agent),
  sent as `Authorization: Bearer <token>` + `X-Faxxme-User: <callsign>`. The token is a
  high-entropy string stored **sha256-hashed** in `users.token_hash`; `POST /api/token/regenerate`
  issues a new one (returned once) and **immediately closes any agent connected with the old
  token** — instant revocation.
- **Node indicator.** When an agent connects/disconnects, the server broadcasts a `{type:node}`
  message to that user's browser tabs, and the PRINTER pill live-updates
  (`ONLINE` browser-USB → `NODE ✓` → `WIRED` → `OFFLINE`). While a node/bridge prints for you,
  the browser's *CONNECT PRINTER* button is hidden (you don't need WebUSB).
- **Test.** `POST /api/test-print` pushes a system-generated test receipt to your agent
  (or host bridge) so **TEST** works even without a browser-bound printer.

## One source of truth: server-side ESC/POS

The receipt bytes are always built on the server (`printer.build_receipt`). The browser
never formats anything — it just forwards the raw bytes over WebUSB. The local bridge
writes the *same* bytes to the device. This keeps WebUSB and the local bridge byte-for-byte
identical, and makes layout/format changes a one-file edit.

Receipt layout:

```
        FAXXME            (double-size, centered)
--------------------------------
FROM: <display name> @<callsign>
TIME: YYYY-MM-DD HH:MM:SS
--------------------------------
<message body, word-wrapped to FAXXME_WIDTH>
[dithered image raster, if attached]
--------------------------------
     .: end of message :.
<feed / cut per FAXXME_CUT>
```

**Unicode text.** Thermal printers only know a legacy code page, so Vietnamese/emoji can't be
sent as bytes. `build_receipt` checks each line: **ASCII** stays fast native ESC/POS text;
anything else (a body line, or a sender name with diacritics) is rendered with a bundled font
(`FAXXME_FONT`, DejaVu Mono Bold) and printed as a **crisp thresholded `GS v 0` raster**
(`imaging.text_raster`) — not dithered, so the strokes stay solid.

## Image attachments

Optional. On the client you pick an image and see a **live Floyd–Steinberg preview**
(canvas). On send, the server (`imaging.process_upload`):

1. fixes EXIF orientation, converts to grayscale, auto-contrasts,
2. resizes to the paper width (`FAXXME_PRINT_DOTS`, default 384 ≈ 58mm), capping height,
3. **Floyd–Steinberg dithers to 1-bit** and stores a compact PNG in the fax row.

At print time `imaging.escpos_raster` packs that PNG into a `GS v 0` raster command placed
below the text. The same PNG is served at `GET /api/fax/{id}/image` for on-screen display
(sender/recipient only).

## Housekeeping

- **Inbox/outbox** show your newest 50; older faxes are auto-pruned per side.
- **Clear** hides faxes from *your* side only (soft-delete flags `sender_deleted` /
  `recipient_deleted`); the other party keeps their copy. A row is physically removed only
  once both sides have cleared it.
- You **can't fax yourself**; messages are capped at 200 characters.

## The printed-receipt modal

Click any fax in inbox/outbox to see it rendered as a **paper slip** — cream paper, torn
zigzag edges, the exact text layout the printer produces. Handy to re-read or screenshot.
