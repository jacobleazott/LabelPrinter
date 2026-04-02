"""
Brother P-touch raster protocol implementation.
"""

from __future__ import annotations

import socket
import struct
import os
from typing import Iterator
from PIL import Image


# ── Raster byte generation ───────────────────────────────────────────────────

def _raw_row(img: Image.Image, img_pixels, stripe_count: int, x: int, y_offset: int) -> bytes:
    """Convert one column (x) of the image into stripe_count bytes."""
    row = bytearray(stripe_count)
    for stripe_idx in range(stripe_count):
        byte = 0
        for bit_index in range(8):
            y = stripe_idx * 8 + bit_index - y_offset
            if 0 <= x < img.width and 0 <= y < img.height:
                px = img_pixels[x, y]
                if isinstance(px, int):
                    val = px
                else:
                    val = int(sum(px[:3]) / 3)
                if val < 230:
                    byte |= 1 << (7 - bit_index)
        row[stripe_idx] = byte
    return bytes(row)


def _generate_raster(
    img: Image.Image,
    stripe_size: int,
    media_width_mm: int,
    top_margin: int = 8,
    bottom_margin: int = 8,
    cut_mode: str = "full",
    y_offset: int = 0,
) -> bytes:
    """
    Generate Brother raster data for a PT-9800PCN.

    img:            PIL Image (width=label_length, height≤stripe_size)
    stripe_size:    Total printable dots across tape width
    media_width_mm: Tape width in mm (sent in init packet)
    top_margin:     Empty lines before image (pixels)
    bottom_margin:  Empty lines after image (pixels)
    cut_mode:       "full", "half", "none"
    """

    import sys
    print(f"DEBUG: stripe_size={stripe_size} img.size={img.size}", file=sys.stderr)
    
    # 9800PCN has a cut correction: the cut comes ~8px after the cut command
    CUT_CORRECTION = 8

    if img.mode != "RGB":
        img = img.convert("RGB")

    pixels = img.load()
    assert stripe_size % 8 == 0
    stripe_count = stripe_size // 8

    # Offset to center image vertically in the stripe, plus profile-level physical correction
    y_offset = (stripe_size - img.height) + y_offset

    # Mode byte: bit6=auto-cut, bit7=mirror
    mode_byte = 0x40 if cut_mode == "full" else 0x00

    buf = bytearray()

    # Sync
    buf += b"\x00" * 200

    # Init sequence
    buf += b"\x1b@"          # ESC @ — initialize
    buf += b"\x1bia\x01"     # raster mode
    buf += bytes([0x1b, 0x69, 0x4d, mode_byte])  # ESC i M — mode settings
    buf += b"\x1bid\x00\x00" # margin = 0

    # 9800PCN-specific media setup
    # \x8e = media type flags; \x01 = unknown; media_width_mm; \x00\x00
    buf += bytes([0x1b, 0x69, 0x63, 0x8e, 0x01, media_width_mm, 0x00, 0x00])

    # Feed correction
    buf += b"\x1bid\x00\x00"

    # Compression: none
    buf += b"\x4d\x00"  # M \x00

    # Top margin (empty lines, accounting for cut correction)
    effective_top = max(0, top_margin - CUT_CORRECTION)
    buf += b"Z" * effective_top

    # Raster data — one column at a time
    for x in range(img.width):
        row = _raw_row(img, pixels, stripe_count, x, y_offset)
        buf += b"G" + struct.pack("<H", len(row)) + row

    # Bottom margin
    buf += b"Z" * (bottom_margin + CUT_CORRECTION)

    # Print / eject
    if cut_mode == "half":
        buf += b"\x1bi\x64\x01"  # half cut command (may need tuning per firmware)
    buf += b"\x1a"

    return bytes(buf)


# ── Network send ─────────────────────────────────────────────────────────────

def send_to_printer(data: bytes, ip: str, port: int = 9100, timeout: float = 10.0) -> None:
    with socket.create_connection((ip, port), timeout=timeout) as sock:
        sock.sendall(data)


# ── High-level print ─────────────────────────────────────────────────────────

def print_label(
    img: Image.Image,
    stripe_size: int,
    media_width_mm: int,
    ip: str,
    port: int = 9100,
    top_margin: int = 8,
    bottom_margin: int = 8,
    cut_mode: str = "full",
    copies: int = 1,
    y_offset: int = 0,
) -> None:
    """Render and send one or more copies of a label to the printer."""
    data = _generate_raster(
        img=img,
        stripe_size=stripe_size,
        media_width_mm=media_width_mm,
        top_margin=top_margin,
        bottom_margin=bottom_margin,
        cut_mode=cut_mode,
        y_offset=y_offset,
    )
    for _ in range(copies):
        send_to_printer(data, ip, port)


# ── Printer status ───────────────────────────────────────────────────────────

def get_printer_status(ip: str, timeout: float = 3.0) -> dict:
    """
    Scrape the printer's web UI for status info.
    Returns a dict with whatever we can parse.
    """
    import requests
    status = {
        "reachable": False,
        "status": "unknown",
        "tape_end_error": False,
        "memory_full": False,
        "busy": False,
        "cover_open": False,
        "printing": False,
        "waiting_for_data": False,
        "firmware": None,
        "serial": None,
        "media_type": None,
        "page_count": None,
        "ip": ip,
    }
    try:
        r = requests.get(
            f"http://{ip}/printer/main.html",
            timeout=timeout,
        )
        html = r.text
        status["reachable"] = True

        import re
        def _find(pattern: str, default=None):
            m = re.search(pattern, html, re.IGNORECASE)
            return m.group(1).strip() if m else default

        status["firmware"] = _find(r"Printer Firmware Version.*?:\s*([\d.]+)")
        status["serial"] = _find(r"Serial no\.\s*:\s*([A-Z0-9]+)")
        status["media_type"] = _find(r"Media Type\s*([^\n<]+)")
        status["page_count"] = _find(r"Total Page Count.*?:\s*(\d+)")

        # Port status page has more detail
        try:
            r2 = requests.get(f"http://{ip}/general/port_status.html", timeout=timeout)
            ps = r2.text
            def _bool(pat: str) -> bool:
                m = re.search(pat, ps, re.IGNORECASE)
                return bool(m and "yes" in m.group(1).lower())

            def _status_str(pat: str) -> str:
                m = re.search(pat, ps, re.IGNORECASE)
                return m.group(1).strip() if m else "unknown"

            status["status"] = _status_str(r"Status\s*\n?\s*(\w+)")
            status["tape_end_error"] = _bool(r"Tape end error\s+(\w+)")
            status["memory_full"] = _bool(r"Memory full\s+(\w+)")
            status["busy"] = _bool(r"Busy\s+(\w+)")
            status["cover_open"] = _bool(r"Cover open\s+(\w+)")
            status["printing"] = _bool(r"Printing\s+(\w+)")
            status["waiting_for_data"] = _bool(r"Waiting for data\s+(\w+)")
        except Exception:
            pass

    except Exception as e:
        status["error"] = str(e)

    return status
