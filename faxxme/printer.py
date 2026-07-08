"""ESC/POS receipt builder + optional local printer bridge (writes to a /dev device).

The browser is the primary print path (WebUSB, client side). The server builds the raw
ESC/POS bytes so there is a single source of truth: the client just forwards them to USB.
The local bridge lets the machine that physically hosts the printer (e.g. this Raspberry Pi)
receive faxes without any browser open at all.
"""
import os
import re
import time

ESC = b"\x1b"
GS = b"\x1d"

# Any control byte (ESC 0x1b, GS 0x1d, …) inside message/header/footer TEXT would be written
# straight to the printer and interpreted as an ESC/POS command. Strip them here — the single
# choke point every print path (browser WebUSB, local bridge, Pi agent) renders through — so no
# fax content can inject printer commands, whatever its source (sender, display name, webhook).
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _text(s: str) -> str:
    return _CTRL_RE.sub("", s)

# --- config for the optional local bridge ---
LOCAL_DEVICE = os.environ.get("FAXXME_PRINTER_DEV", "/dev/usb/lp0")
LOCAL_USER = os.environ.get("FAXXME_LOCAL_USER")  # username whose faxes print on this host
LOCAL_WIDTH = int(os.environ.get("FAXXME_WIDTH", "32"))  # chars per line (58mm ~= 32)
CUT_MODE = os.environ.get("FAXXME_CUT", "full").lower()  # full | partial | feed | none
FOOTER_FONT_SIZE = int(os.environ.get("FAXXME_FOOTER_FONT_SIZE", "22"))  # small attribution raster


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


def _unicode_raster(text: str, size: int | None = None) -> bytes:
    """A non-ASCII text line rendered via a Unicode font, left-aligned, as an ESC/POS raster.
    `size` overrides the font size (used by the small attribution footer)."""
    from . import imaging
    return ESC + b"a" + b"\x00" + imaging.text_raster(text, size=size) + b"\n"


def build_receipt(sender_display: str, sender_username: str, body: str,
                  created_at: float, width: int = LOCAL_WIDTH,
                  image_escpos: bytes | None = None, footer: str = "") -> bytes:
    """Render a fax as ESC/POS bytes. `image_escpos` is an optional pre-built raster
    command (from imaging.escpos_raster) printed below the text. `footer` is an optional
    attribution block (e.g. a webhook's sender name + source) printed in a smaller font."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at))
    d = bytearray()
    d += ESC + b"@"                      # initialize
    d += ESC + b"a" + b"\x01"            # center
    d += ESC + b"!" + b"\x38"            # double width/height + bold
    d += b"FAXXME\n"
    d += ESC + b"!" + b"\x00"            # normal
    d += b"-" * width + b"\n"
    d += ESC + b"a" + b"\x00"            # left
    from_line = _text(f"FROM: {sender_display} @{sender_username}")   # strip control bytes
    if from_line.isascii():
        d += (from_line[:width] + "\n").encode("ascii", "replace")
    else:                                # a name with diacritics -> render as raster
        d += _unicode_raster(from_line)
    d += f"TIME: {ts}\n".encode("ascii", "replace")
    d += b"-" * width + b"\n"
    if body:
        for para in body.replace("\r\n", "\n").split("\n"):
            para = _text(para)           # neutralize any embedded ESC/POS control bytes
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
    if footer.strip():                   # attribution block, one small raster so every line
        d += b"\n"                        # (name, post, url) looks identical and smaller than
        # keep the line breaks (raster splits on them), strip other control bytes
        clean_footer = "\n".join(_text(ln) for ln in footer.strip("\n").split("\n"))
        d += _unicode_raster(clean_footer, size=FOOTER_FONT_SIZE)          # the message
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


WRITE_CHUNK = int(os.environ.get("FAXXME_WRITE_CHUNK", "4096"))    # bytes per os.write to the device
WRITE_DELAY = float(os.environ.get("FAXXME_WRITE_DELAY", "0"))     # seconds to pause between chunks


def _write_all(fd: int, data: bytes) -> bool:
    """Write every byte, tolerating short writes. A single os.write to a USB printer char device
    often accepts only part of a large buffer and returns a short count; without looping the rest
    is silently dropped — long messages/images then print truncated. Chunking also lets the
    (blocking) device apply backpressure; `FAXXME_WRITE_DELAY` adds a pause between chunks to pace
    a printer whose buffer can't keep up. Runs in a worker thread (see app._bridge_print)."""
    view = memoryview(data)
    total = len(view)
    sent = 0
    while sent < total:
        w = os.write(fd, view[sent:sent + WRITE_CHUNK])
        if w <= 0:
            return False
        sent += w
        if WRITE_DELAY and sent < total:
            time.sleep(WRITE_DELAY)
    return True


def print_local(data: bytes) -> bool:
    """Write raw ESC/POS bytes straight to the local printer device. Returns True once all bytes
    are written — a failure while *closing* the fd (e.g. the printer dropped off USB right after a
    full print) must not mask a successful write, or the fax gets reprinted."""
    fd = None
    try:
        fd = os.open(LOCAL_DEVICE, os.O_WRONLY)
        return _write_all(fd, data)
    except OSError:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
