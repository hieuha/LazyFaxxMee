# Printer compatibility

> 🌐 Language: **English** · [Tiếng Việt](vi/printers.md)

FaxxMe speaks **raw ESC/POS** — the command language virtually every cheap USB/Bluetooth
thermal receipt printer understands. There are three ways bytes reach a printer; each has
different requirements.

## The print paths

| path | where it runs | needs | best for |
|------|---------------|-------|----------|
| **Local bridge** | the server host | printer wired to the host; a writable `/dev/usb/lp*` (Linux) | a Raspberry Pi / always-on box hosting the app + printer |
| **Printer node (agent)** | the recipient's own Pi | the [agent](../agent/README.md) + a device token; a writable `/dev/usb/lp*` | each user having their own printer, anywhere with internet |
| **Browser WebUSB** | the recipient's browser | Chromium, HTTPS/localhost, a *claimable* USB interface | a printer plugged into the same computer as the browser |
| **Browser print** (fallback) | the recipient's browser | any OS-installed printer + driver | one-off manual prints (needs a click; can't auto-print) |

The **local bridge** and the **printer-node agent** are the most reliable paths (server-side
ESC/POS → `/dev/usb/lp*`, no browser quirks). WebUSB is finicky and OS-dependent — see
[platforms.md](platforms.md).

## ⚡ Power & USB — the #1 cause of truncated prints and reprint loops

**Do not power a thermal printer from a Raspberry Pi's USB port.** A thermal print head draws
large current spikes while burning dots, and battery/"mobile" printers *also* draw charging
current over the same USB cable. A Pi's USB port can't supply that — the Pi's **over-current
protection cuts power to the port**, the printer **drops off the USB bus mid-print**, and then
re-enumerates a second later. On repeat this looks like:

- a long message or image prints only **part way**, then stops (the link died mid-write); and/or
- the **same fax prints over and over** while the UI stays on **`queued`** (the printer
  disconnected right after printing, so its "done" acknowledgement never reached the server, so
  the fax was re-queued and reprinted).

**How to confirm it's power** (run on the host):

```bash
dmesg | grep -iE "over-current|usblp0|disconnect" | tail    # over-current + "usblp0: removed" looping = this bug
dmesg | grep -c over-current                                # a number that keeps climbing = ongoing
vcgencmd get_throttled                                       # 0x0 = Pi core supply OK; non-zero = under-voltage too
```

If you see `over-current change` and `usblp0: removed` repeating (even while idle), it's power — no
software setting can fix it.

**The fix (pick one):**

1. **Powered USB hub** (best) — plug the printer into a hub that has its **own** power adapter, and
   the hub into the Pi. The printer draws current from the hub's adapter, not the Pi.
2. **Power the printer from its own supply** — for a mobile/battery printer, **charge it from a
   wall charger** and use the Pi's USB for *data only*; for a printer with a barrel-jack, use it.
3. Use an **adequate Pi power supply** (official 5V/3A+ USB-C for Pi 4/5) and a **good short USB
   cable** — a weak PSU or thin/long cable makes it worse. On some Pis, `max_usb_current=1` in
   `/boot/firmware/config.txt` raises the per-port budget, but a powered hub is the real fix.

**Software safety net:** if writes keep failing, FaxxMe/the agent **gives up after
`FAXXME_BRIDGE_MAX_ATTEMPTS` tries** (default `3`) and marks the fax delivered, so a flaky printer
can't reprint forever. That bounds the damage — it does **not** replace fixing the power.

## What works well

Any **USB ESC/POS thermal printer** that the OS exposes as a raw line printer. Known-good
families:

- **58 mm mini/portable** — GOOJPRT PT-210 / PT-280, GDMicroelectronics "micro-printer"
  (USB id `28e9:0289`, the unit this project was developed against), MUNBYN, Xprinter,
  Rongta, Zjiang. These are `class 07` USB printers → `/dev/usb/lp0` on Linux.
- **80 mm desktop receipt printers** — Epson TM-T20/T88 (ESC/POS mode), Bixolon, Xprinter
  80 mm. Set `FAXXME_WIDTH=48` and `FAXXME_PRINT_DOTS=576`.

Rule of thumb: **if it prints from a generic ESC/POS app, it works with FaxxMe.**

## Configure for your paper width

| paper | `FAXXME_WIDTH` (text cols) | `FAXXME_PRINT_DOTS` (image px) |
|-------|---------------------------|-------------------------------|
| 58 mm | `32` (default) | `384` (default) |
| 80 mm | `48` | `576` |

If text wraps oddly or the image is too narrow/clipped, these two are what to tune.

## Vietnamese / Unicode text

Thermal printers only know a legacy code page (usually CP437), so accented Vietnamese
(`ế ộ ậ ượ`), emoji, CJK, etc. can't be sent as bytes — most printers would print `?`.
FaxxMe handles this automatically: a line that is **pure ASCII** is printed as fast native
ESC/POS text, while a line with **any non-ASCII** character (a message line, or a sender name
with diacritics) is **rendered with a bundled font and printed as a `GS v 0` raster** — so it
works on *any* ESC/POS printer regardless of its code page. Tuning:

| var | default | meaning |
|-----|---------|---------|
| `FAXXME_FONT` | bundled Google Fonts **Play** | any TTF with the glyphs you need |
| `FAXXME_FONT_SIZE` | `26` | bigger = clearer on thermal (but fewer chars per line, more paper) |
| `FAXXME_FONT_THRESHOLD` | `176` | black/white cutoff — raise it if text looks faint |

The rendered text is **thresholded, not dithered**, so strokes stay solid and crisp.

## Auto-cut

`FAXXME_CUT` controls the end-of-fax cut (sent as an ESC/POS command; printers without a
cutter simply ignore it):

- `full` (default) — small feed + full cut. Safe everywhere.
- `feed` — feed-to-cutter + full cut (`GS V 66`). **Cleanest & least paper, but only if the
  printer actually has a cutter** (otherwise it won't feed for tear-off).
- `partial` — leaves a small uncut tab.
- `none` — no cut, just feed for manual tearing.

To tell if your printer has a cutter: send a test fax with `FAXXME_CUT=full`. If the paper
is cut, it has a cutter (switch to `feed`); if not, it's tear-only (keep `full`).

## Caveats & unsupported cases

- **Bluetooth-/serial-only printers** — FaxxMe's local bridge and WebUSB target USB. A
  printer that only exposes a serial/Bluetooth SPP interface won't be reached (a Web Serial
  path could be added — open an issue if you need it).
- **Very old printers** that only support the `ESC *` bit-image command (not `GS v 0`) will
  print the **text** fine but may skip or garble **images**.
- **GDI / "driver-only" printers** (many label printers, host-based inkjets) don't accept
  raw ESC/POS at all — use the **browser print** fallback for those.
- **Image size** — tall images are capped at `FAXXME_IMG_MAX_H` dots; uploads at
  `FAXXME_MAX_UPLOAD` bytes (6 MB). Dithering happens server-side, so huge images just cost
  a little CPU on send.

## Non-thermal printers

Anything the browser can claim over WebUSB will receive the bytes, but a normal inkjet/laser
won't understand ESC/POS — for those use the **browser print** fallback, which renders the
fax as an HTML receipt and sends it through the OS print dialog to any installed printer.
