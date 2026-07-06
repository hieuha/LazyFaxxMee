"""Image → 1-bit halftone (Floyd–Steinberg dither) → ESC/POS raster bytes.

Thermal printers are 1 bit per dot, so photos are error-diffusion dithered to black/white.
We keep a compact dithered PNG (for on-screen display + browser-print fallback) and, at print
time, pack it into a `GS v 0` raster command for the actual printer.
"""
import io
import os

from PIL import Image, ImageOps

DOTS = int(os.environ.get("FAXXME_PRINT_DOTS", "384"))     # printable dots across (58mm ≈ 384)
MAX_H = int(os.environ.get("FAXXME_IMG_MAX_H", "1200"))    # cap print height (dots)
MAX_UPLOAD = int(os.environ.get("FAXXME_MAX_UPLOAD", str(6 * 1024 * 1024)))  # 6 MB

GS = b"\x1d"


def process_upload(raw: bytes, dots: int = DOTS, max_h: int = MAX_H) -> tuple[bytes, int, int]:
    """Decode any image, fix orientation, grayscale, auto-contrast, resize to paper width,
    and Floyd–Steinberg dither to 1-bit. Returns (png_bytes, width, height)."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)          # respect phone-photo rotation
    img = img.convert("L")                      # grayscale
    img = ImageOps.autocontrast(img, cutoff=1)  # stretch levels for a cleaner dither

    w = max(1, dots)
    h = max(1, round(img.height * w / img.width))
    if h > max_h:                               # keep aspect, cap very tall images
        scale = max_h / h
        w = max(1, round(w * scale))
        h = max_h
    img = img.resize((w, h), Image.LANCZOS)

    bw = img.convert("1")                        # mode "1" convert = Floyd–Steinberg dither
    out = io.BytesIO()
    bw.save(out, format="PNG", optimize=True)
    return out.getvalue(), w, h


def escpos_raster(png_bytes: bytes) -> bytes:
    """Pack a 1-bit PNG into an ESC/POS `GS v 0` raster bit-image command."""
    img = Image.open(io.BytesIO(png_bytes)).convert("1")
    w, h = img.size
    px = img.load()
    width_bytes = (w + 7) // 8

    raster = bytearray(width_bytes * h)
    for y in range(h):
        row = y * width_bytes
        for x in range(w):
            if px[x, y] == 0:                    # black dot -> set bit (MSB first)
                raster[row + (x >> 3)] |= 0x80 >> (x & 7)

    cmd = bytearray()
    cmd += GS + b"v0" + b"\x00"                   # GS v 0, mode 0 (normal)
    cmd += bytes([width_bytes & 0xFF, (width_bytes >> 8) & 0xFF])
    cmd += bytes([h & 0xFF, (h >> 8) & 0xFF])
    cmd += raster
    return bytes(cmd)
