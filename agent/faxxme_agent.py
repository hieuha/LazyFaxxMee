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
    try:
        fd = os.open(DEVICE, os.O_WRONLY)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return True
    except OSError as e:
        log("device write failed:", e)
        return False


async def run_once():
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Faxxme-User": USER}
    async with websockets.connect(ws_url(), additional_headers=headers,
                                  ping_interval=20, ping_timeout=20) as ws:
        log("connected to", ws_url(), "as", USER)
        pending: dict[int, bytes] = {}     # fax_id -> ready-to-print ESC/POS bytes
        lock = asyncio.Lock()

        async def flush():
            async with lock:               # serialize printing so a fax never prints twice
                for fid in list(pending):
                    if device_writable() and write_device(pending[fid]):
                        await ws.send(json.dumps({"type": "ack", "fax_id": fid}))
                        del pending[fid]
                        log("printed fax", fid)
                    else:
                        return             # printer offline -> try again on the next tick
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

        await asyncio.gather(receive_loop(), retry_loop())


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
