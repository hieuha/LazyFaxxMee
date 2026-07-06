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
CUT_MODE = os.environ.get("FAXXME_CUT", "full").lower()  # full | partial | feed | none


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


def _unicode_raster(text: str) -> bytes:
    """A non-ASCII text line rendered via a Unicode font, left-aligned, as an ESC/POS raster."""
    from . import imaging
    return ESC + b"a" + b"\x00" + imaging.text_raster(text) + b"\n"


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
    d += b"-" * width + b"\n"
    d += ESC + b"a" + b"\x00"            # left
    from_line = f"FROM: {sender_display} @{sender_username}"
    if from_line.isascii():
        d += (from_line[:width] + "\n").encode("ascii", "replace")
    else:                                # a name with diacritics -> render as raster
        d += _unicode_raster(from_line)
    d += f"TIME: {ts}\n".encode("ascii", "replace")
    d += b"-" * width + b"\n"
    if body:
        for para in body.replace("\r\n", "\n").split("\n"):
            if para == "":
                d += b"\n"
            elif para.isascii():         # fast, crisp native ESC/POS text
                d += ESC + b"!" + b"\x08"
                for line in _wrap(para, width):
                    d += (line + "\n").encode("ascii", "replace")
                d += ESC + b"!" + b"\x00"
            else:                        # Vietnamese / emoji / any Unicode -> font raster
                d += _unicode_raster(para)
    if image_escpos:
        d += ESC + b"a" + b"\x01"        # center the image
        d += image_escpos
        d += b"\n"
        d += ESC + b"a" + b"\x00"
    d += b"-" * width + b"\n"
    d += ESC + b"a" + b"\x01"            # center
    d += b".: end of message :.\n"
    d += _cut()
    return bytes(d)


def _cut() -> bytes:
    """Trailing feed and/or auto-cut, selected by FAXXME_CUT.

    full    (default) small feed + full cut     — cutter cuts, tear-off printers ignore + tear
    feed              feed-to-cutter + full cut — cleanest & least paper on printers WITH a cutter
    partial           small feed + partial cut
    none              feed only, tear by hand
    """
    if CUT_MODE == "none":
        return b"\n\n\n"
    if CUT_MODE == "feed":
        return GS + b"VB" + b"\x00"          # GS V 66 0: feed paper to the cutter, then full cut
    if CUT_MODE == "partial":
        return b"\n\n" + GS + b"V" + b"\x01"
    return b"\n\n" + GS + b"V" + b"\x00"      # full (default)


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
