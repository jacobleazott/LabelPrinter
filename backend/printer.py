from __future__ import annotations

import socket
import struct
import time
import sys
from pathlib import Path
from typing import Dict, Any, Optional

from PIL import Image


PROFILES_PATH = Path(__file__).parent.parent / "data" / "tapes.json"


def load_profiles() -> Dict[str, Dict[str, Any]]:
    import json
    with open(PROFILES_PATH) as f:
        return json.load(f)


def _get_profile_value(profile: Dict[str, Any], *names: str, default=None):
    for name in names:
        if name in profile:
            return profile[name]
    return default


def _apply_vertical_padding(img, pad_top=0, pad_bottom=0):
    pad_top = int(pad_top or 0)
    pad_bottom = int(pad_bottom or 0)

    if pad_top == 0 and pad_bottom == 0:
        return img

    canvas = Image.new(
        "RGB",
        (img.width, img.height + pad_top + pad_bottom),
        (255, 255, 255),
    )
    canvas.paste(img, (0, pad_top))
    return canvas


def _media_setup_bytes(profile: Dict[str, Any]) -> bytes:
    media_type_byte = int(
        _get_profile_value(profile, "media_type_byte", "media_init_byte")
    )
    media_width_byte = int(
        _get_profile_value(profile, "media_width_byte", "media_width_code")
    )

    return bytes([
        0x1B, 0x69, 0x63,
        media_type_byte,
        0x01,
        media_width_byte,
        0x00, 0x00,
    ])


def _raw_row(img: Image.Image, img_pixels, stripe_count: int, x: int, y_offset: int) -> bytes:
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


def _compress_tiff(row):
    pos = 0
    uncompressed_start = pos
    while pos < len(row):
        count = 0
        while pos + count + 1 < len(row) and row[pos + count + 1] == row[pos + count]:
            count += 1

        if count > 0:
            if uncompressed_start < pos:
                yield struct.pack("!b", pos - uncompressed_start - 1) + row[uncompressed_start:pos]
            yield struct.pack("!bB", -count, row[pos])
            pos += count + 1
            uncompressed_start = pos
        else:
            pos += 1

    if uncompressed_start < pos:
        yield struct.pack("!b", pos - uncompressed_start - 1) + row[uncompressed_start:pos]


def _generate_raster(
    img: Image.Image,
    profile: Dict[str, Any],
    cut_mode: str = "full",
) -> bytes:
    """
    Generate Brother raster data for a PT-9800PCN.

    Profile fields used:
        stripe_size
        media_type_byte   (or media_init_byte)
        media_width_byte  (or media_width_code)
        content_pad_top_dots
        content_pad_bottom_dots
        mode_byte         (optional)
    """
    stripe_size = int(profile["stripe_size"])

    if img.mode != "RGB":
        img = img.convert("RGB")

    print(f"DEBUG: img.height {img.height} img.width {img.width}")
    # Per-tape calibration padding.
    img = _apply_vertical_padding(
        img,
        pad_top=int(profile.get("content_pad_top_dots", 0)),
        pad_bottom=int(profile.get("content_pad_bottom_dots", 0)),
    )

    print(f"DEBUG: img.height {img.height} img.width {img.width}")

    pixels = img.load()
    # assert img.height % 8 == 0
    stripe_count = img.height // 8

    # Leave this data-driven too, in case you later need per-tape cut/mirror behavior.
    mode_byte = int(profile.get("mode_byte", 0x00))

    buf = bytearray()
    buf += b"\x00" * 200

    buf += b"\x1b@"          # ESC @
    buf += b"\x1bia\x01"     # raster mode
    buf += bytes([0x1B, 0x69, 0x4D, mode_byte])
    buf += b"\x1bid\x00\x00"  # feed margin = 0 for now

    media_type_byte = int(profile.get("media_type_byte", 0x8e))
    media_width_byte = int(profile.get("media_width_byte", 0x18))

    buf += bytes([0x1B, 0x69, 0x63, media_type_byte, 0x01, media_width_byte, 0x00, 0x00])

    buf += b"\x1bid\x00\x00"  # feed correction
    buf += b"\x4d\x02"        # TIFF compression

    # Raster data — one column at a time, which is what your working pipeline expects.
    for x in range(img.width):
        row = _raw_row(img, pixels, stripe_count, x, y_offset=0)
        compressed = b"".join(_compress_tiff(row))
        buf += b"G" + struct.pack("<H", len(compressed)) + compressed

    buf += b"\x1a"

    return bytes(buf)


def send_to_printer(data: bytes, ip: str, port: int = 9100, timeout: float = 30.0) -> None:
    print(f"DEBUG: compressed raster bytes={len(data)}", file=sys.stderr)
    with socket.create_connection((ip, port), timeout=timeout) as sock:
        CHUNK_SIZE = 4096
        for i in range(0, len(data), CHUNK_SIZE):
            sock.sendall(data[i:i + CHUNK_SIZE])
            time.sleep(0.01)
        sock.shutdown(socket.SHUT_WR)
        time.sleep(2)


def print_label(
    img: Image.Image,
    tape_profile_key: str,
    ip: str,
    port: int = 9100,
    copies: int = 1,
    cut_mode: str = "full",
) -> None:
    profiles = load_profiles()
    if tape_profile_key not in profiles:
        raise KeyError(f"Unknown tape profile: {tape_profile_key}")

    profile = profiles[tape_profile_key]
    data = _generate_raster(
        img=img,
        profile=profile,
        cut_mode=cut_mode,
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
