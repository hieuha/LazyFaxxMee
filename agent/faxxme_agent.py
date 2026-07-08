#!/usr/bin/env python3
"""FaxxMe printer agent — turn a Raspberry Pi into a headless "printer node".

It signs in to a FaxxMe server with your callsign + device token (never your password),
opens the same WebSocket the browser uses, and prints every fax addressed to you straight
to the local thermal printer. Faxes that arrive while the printer is unplugged are held and
printed the moment it comes back.

Config comes from the environment (see faxxme-agent.env.example):
  FAXXME_SERVER        e.g. https://faxx.example.ts.net   (or http://host:8000)
  FAXXME_AGENT_USER    your callsign
  FAXXME_AGENT_TOKEN   the device token from the web (:: PRINTER NODE → GENERATE TOKEN)
  FAXXME_PRINTER_DEV   printer device node (default /dev/usb/lp0)
  FAXXME_PRINTER_POLL  seconds between retries while the printer is offline (default 4)
"""
import asyncio
import base64
import json
import os
import sys
import time

import websockets

SERVER = os.environ.get("FAXXME_SERVER", "").strip().rstrip("/")
USER = os.environ.get("FAXXME_AGENT_USER", "").strip()
TOKEN = os.environ.get("FAXXME_AGENT_TOKEN", "").strip()
DEVICE = os.environ.get("FAXXME_PRINTER_DEV", "/dev/usb/lp0")
POLL = float(os.environ.get("FAXXME_PRINTER_POLL", "4"))
# If a write keeps failing (e.g. an underpowered printer that stalls/re-enumerates mid-print),
# ack + drop the fax after this many attempts instead of reprinting it forever.
MAX_ATTEMPTS = int(os.environ.get("FAXXME_BRIDGE_MAX_ATTEMPTS", "3"))
AGENT_UA = "FaxxMe-Agent/0.1 (+https://github.com/hieuha/LazyFaxxMee)"


def log(*a):
    print("[faxxme-agent]", *a, flush=True)


def ws_url() -> str:
    if SERVER.startswith("https://"):
        return "wss://" + SERVER[len("https://"):] + "/ws"
    if SERVER.startswith("http://"):
        return "ws://" + SERVER[len("http://"):] + "/ws"
    if SERVER.startswith(("ws://", "wss://")):
        return SERVER + "/ws"
    return "wss://" + SERVER + "/ws"          # bare host -> assume TLS


def device_writable() -> bool:
    return os.access(DEVICE, os.W_OK)


def write_device(data: bytes) -> bool:
    # Loop until every byte is written: a single os.write to a USB printer often accepts only part
    # of a large buffer (returns a short count), so long messages/images would otherwise print
    # truncated. Chunking also lets the blocking device throttle us to the printer's buffer.
    # Return True as soon as all bytes are written — a failure while *closing* the fd (printer
    # dropped off USB right after a full print) must not mask a successful write, or we'd reprint.
    fd = None
    try:
        fd = os.open(DEVICE, os.O_WRONLY)
        view = memoryview(data)
        total, sent = len(view), 0
        while sent < total:
            w = os.write(fd, view[sent:sent + 4096])
            if w <= 0:
                return False
            sent += w
        return True
    except OSError as e:
        log("device write failed:", e)
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


async def run_once():
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Faxxme-User": USER}
    async with websockets.connect(ws_url(), additional_headers=headers,
                                  user_agent_header=AGENT_UA,
                                  ping_interval=20, ping_timeout=20) as ws:
        log("connected to", ws_url(), "as", USER)
        pending: dict[int, bytes] = {}     # fax_id -> ready-to-print ESC/POS bytes
        attempts: dict[int, int] = {}      # fax_id -> failed write attempts (for the give-up cap)
        lock = asyncio.Lock()

        async def _ack(fid):
            await ws.send(json.dumps({"type": "ack", "fax_id": fid}))
            del pending[fid]
            attempts.pop(fid, None)

        async def flush():
            async with lock:               # serialize printing so a fax never prints twice
                for fid in list(pending):
                    if not device_writable():
                        return             # printer truly offline -> try again on the next tick
                    if write_device(pending[fid]):
                        await _ack(fid)
                        log("printed fax", fid)
                    else:
                        n = attempts.get(fid, 0) + 1
                        attempts[fid] = n
                        log(f"print failed for fax {fid} (attempt {n}/{MAX_ATTEMPTS})")
                        if n >= MAX_ATTEMPTS:
                            await _ack(fid)   # give up: stop reprinting; check printer power/USB
                            log(f"gave up on fax {fid} — check the printer's power/USB cable")
                        else:
                            return         # retry this fax on the next tick
            return

        async def retry_loop():
            while True:
                await asyncio.sleep(POLL)
                if pending:
                    if not device_writable():
                        log(f"{len(pending)} fax(es) waiting — printer offline at {DEVICE}")
                    await flush()

        async def receive_loop():
            async for raw in ws:
                m = json.loads(raw)
                t = m.get("type")
                if t == "fax":
                    pending[m["id"]] = base64.b64decode(m["escpos_b64"])
                    log("incoming fax", m["id"], "from", m.get("from_username"))
                    await flush()
                elif t == "hello":
                    log("authenticated as", m.get("user", {}).get("username", USER))

        async def heartbeat_loop():
            # app-level ping so the server keeps this node's "last seen" fresh
            while True:
                await asyncio.sleep(30)
                try:
                    await ws.send(json.dumps({"type": "ping"}))
                except Exception:
                    return

        await asyncio.gather(receive_loop(), retry_loop(), heartbeat_loop())


async def main():
    if not (SERVER and USER and TOKEN):
        log("ERROR: set FAXXME_SERVER, FAXXME_AGENT_USER and FAXXME_AGENT_TOKEN "
            "(see faxxme-agent.env.example)")
        sys.exit(2)
    log("printer device:", DEVICE, "(writable now:", device_writable(), ")")
    delay = 3
    while True:
        t0 = time.monotonic()
        try:
            await run_once()
            log("connection closed by server")
        except (OSError, websockets.WebSocketException) as e:
            log("connection error:", e)
        except Exception as e:  # noqa: BLE001 — keep the agent alive no matter what
            log("unexpected error:", e)
        if time.monotonic() - t0 > 30:
            delay = 3                                  # was up a while -> reset backoff
        log(f"reconnecting in {delay}s…")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
