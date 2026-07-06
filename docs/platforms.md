# Platform notes (Windows / Ubuntu / macOS)

These notes are only about the **browser WebUSB** print path — binding a printer plugged
into the *same computer as the browser*. If you use the **local bridge** (printer wired to
the server host), none of this applies: the host prints server-side and any device works.

## Two universal rules for WebUSB

1. **Secure context.** Browsers expose `navigator.usb` only on **HTTPS** or **`localhost`**.
   Plain `http://<LAN-or-tailscale-ip>:8000` from another machine → WebUSB is blocked.
   Fixes: `tailscale serve` (real HTTPS), a reverse proxy with TLS, or Chrome's
   `chrome://flags/#unsafely-treat-insecure-origin-as-secure` for testing.
2. **Chromium only.** WebUSB works in Chrome / Edge / Brave / Opera. **Safari and Firefox
   do not support WebUSB at all.**

The other requirement is that the printer's USB interface must be **claimable** — i.e. not
already held by an OS driver. That's where the three platforms differ.

---

## 🐧 Ubuntu / Linux

**Best supported for WebUSB** — Linux lets Chrome detach kernel drivers.

- When you plug in an ESC/POS printer, the kernel binds `usblp` and creates `/dev/usb/lp0`.
  That's perfect for the **local bridge**, but it *blocks* WebUSB from claiming the same
  interface.
- To use **WebUSB** on Linux, free the interface first:
  ```bash
  sudo modprobe -r usblp          # unbind the kernel printer driver
  ```
  …then the device appears in Chrome's picker and can be claimed. **Note:** this disables
  the local bridge (no more `/dev/usb/lp0`), so pick one path or the other.
- Chrome also needs permission to open the raw USB node. A udev rule grants it:
  ```
  # /etc/udev/rules.d/99-webusb.rules
  SUBSYSTEM=="usb", ATTR{idVendor}=="28e9", MODE="0666"
  ```
  (replace `28e9` with your printer's vendor id from `lsusb`), then
  `sudo udevadm control --reload-rules && sudo udevadm trigger`.
- **On the server host (e.g. Raspberry Pi)** you usually want the opposite: keep `usblp`
  loaded and use the local bridge. `deploy/install.sh` sets that up (udev rule + `lp` group).

## 🍎 macOS

**WebUSB to a printer basically won't work** — and it's not a FaxxMe bug.

- macOS automatically claims **class-compliant USB printers** (like the PT-280) into its
  own print subsystem. Chrome can't detach that driver on macOS, so the printer **does not
  appear** in the WebUSB picker → you get *"No compatible devices found."*
- There's no user-friendly "unbind driver" equivalent to Linux's `modprobe -r`.
- Safari/Firefox don't support WebUSB anyway.

**What to do on a Mac:**

- **Recommended:** don't use the Mac's printer via the browser. Put the printer on a
  Raspberry Pi / Linux host and use the **local bridge** — the Mac just faxes that callsign.
- Or use the **browser print** fallback (OS print dialog) for manual, on-click prints.
- Devices that enumerate as a **vendor-specific** interface (e.g. some ESP32 gadgets) *do*
  show up in WebUSB on macOS — but standard thermal printers don't.

## 🪟 Windows

Similar to macOS: Windows binds its **usbprint** driver to printer-class devices, so they
won't appear in Chrome's WebUSB picker by default.

- To force a device onto the generic **WinUSB** driver (so WebUSB can claim it), use
  **[Zadig](https://zadig.akeo.ie/)** to reassign the driver for that printer. This makes it
  WebUSB-accessible but **removes it from the normal Windows printing system** — a deliberate
  trade-off, and fiddly. Only worth it for a dedicated FaxxMe printer.
- Chrome/Edge only (no Firefox WebUSB).
- **Easiest path, as everywhere:** run the server on a Linux host with the printer attached
  and use the **local bridge**; Windows clients just fax the callsign.

---

## TL;DR

| you're on… | want the printer on this machine via browser? | do this |
|------------|-----------------------------------------------|---------|
| Ubuntu/Linux | yes | `sudo modprobe -r usblp` + udev rule + HTTPS, Chrome |
| macOS | not really possible | use the **local bridge** on a Linux host, or browser-print fallback |
| Windows | possible but fiddly | Zadig → WinUSB + HTTPS, Chrome; else local bridge |
| **any** | **just want it to work** | **wire the printer to a Linux host / Pi and use the local bridge** |
