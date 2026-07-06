"""ESC/POS receipt builder + optional local printer bridge (writes to a /dev device).

The browser is the primary print path (WebUSB, client side). The server builds the raw
ESC/POS bytes so there is a single source of truth: the client just forwards them to USB.
The local bridge lets the machine that physically hosts the printer (e.g. this Raspberry Pi)
receive faxes without any browser open at all.
"""
import os
import time

ESC = b"\x1b"
GS = b"\x1d"

# --- config for the optional local bridge ---
LOCAL_DEVICE = os.environ.get("FAXXME_PRINTER_DEV", "/dev/usb/lp0")
LOCAL_USER = os.environ.get("FAXXME_LOCAL_USER")  # username whose faxes print on this host
LOCAL_WIDTH = int(os.environ.get("FAXXME_WIDTH", "32"))  # chars per line (58mm ~= 32)


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        if raw_line == "":
            out.append("")
            continue
        line = ""
        for word in raw_line.split(" "):
            while len(word) > width:  # hard-break very long words
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:width])
                word = word[width:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def build_receipt(sender_display: str, sender_username: str, body: str,
                  created_at: float, width: int = LOCAL_WIDTH,
                  image_escpos: bytes | None = None) -> bytes:
    """Render a fax as ESC/POS bytes. `image_escpos` is an optional pre-built raster
    command (from imaging.escpos_raster) printed below the text."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at))
    d = bytearray()
    d += ESC + b"@"                      # initialize
    d += ESC + b"a" + b"\x01"            # center
    d += ESC + b"!" + b"\x38"            # double width/height + bold
    d += b"FAXXME\n"
    d += ESC + b"!" + b"\x00"            # normal
    d += b"= incoming transmission =\n"
    d += b"-" * width + b"\n"
    d += ESC + b"a" + b"\x00"            # left
    from_line = f"FROM: {sender_display} @{sender_username}"
    if len(from_line) > width:           # keep it to one line on narrow paper
        from_line = from_line[:width]
    d += (from_line + "\n").encode("ascii", "replace")
    d += f"TIME: {ts}\n".encode("ascii", "replace")
    d += b"-" * width + b"\n"
    if body:
        d += ESC + b"!" + b"\x08"        # emphasized body
        for line in _wrap(body, width):
            d += (line + "\n").encode("ascii", "replace")
        d += ESC + b"!" + b"\x00"
    if image_escpos:
        d += ESC + b"a" + b"\x01"        # center the image
        d += image_escpos
        d += b"\n"
        d += ESC + b"a" + b"\x00"
    d += b"-" * width + b"\n"
    d += ESC + b"a" + b"\x01"            # center
    d += b".: end of message :.\n"
    d += b"\n\n\n\n"                     # feed for tear-off
    d += GS + b"V" + b"\x00"             # full cut (ignored if unsupported)
    return bytes(d)


def local_available() -> bool:
    return LOCAL_USER is not None and os.access(LOCAL_DEVICE, os.W_OK)


def print_local(data: bytes) -> bool:
    """Write raw ESC/POS bytes straight to the local printer device."""
    try:
        fd = os.open(LOCAL_DEVICE, os.O_WRONLY)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False
