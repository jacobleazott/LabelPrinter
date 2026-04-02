"""
Label renderer: converts a LabelSpec into a PIL Image.

Coordinate convention:
  - Image width  = label length (variable, left → right)
  - Image height = stripe_size  (fixed by tape, top → bottom)

The grid layout:
  - Rows divide the stripe height proportionally by their weight.
  - Cells within a row divide the row width proportionally by their weight.
  - Label width is determined by the widest row's natural content width,
    then all rows are stretched to that width via cell weight distribution.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# ── Font index ──────────────────────────────────────────────────────────────

FONT_SEARCH_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.local/share/fonts"),
]

_FONT_INDEX: Optional[Dict[str, str]] = None  # display_name -> absolute_path
_FALLBACK_FONT = None


def _build_font_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for d in FONT_SEARCH_DIRS:
        for root, _, files in os.walk(d):
            for fname in files:
                if fname.lower().endswith(".ttf"):
                    path = os.path.join(root, fname)
                    name = fname[:-4]  # strip .ttf
                    index[name] = path
    return dict(sorted(index.items()))


def font_index() -> Dict[str, str]:
    global _FONT_INDEX
    if _FONT_INDEX is None:
        _FONT_INDEX = _build_font_index()
    return _FONT_INDEX


def resolve_font(name: str) -> str:
    """Return the absolute path for a font name, with fuzzy fallback."""
    idx = font_index()
    if name in idx:
        return idx[name]
    # Case-insensitive search
    lower = name.lower()
    for k, v in idx.items():
        if k.lower() == lower:
            return v
    # Partial match
    for k, v in idx.items():
        if lower in k.lower():
            return v
    # Ultimate fallback
    fallbacks = [
        "LiberationSans-Bold",
        "DejaVuSans-Bold",
        "DejaVuSans",
    ]
    for fb in fallbacks:
        if fb in idx:
            return idx[fb]
    return next(iter(idx.values())) if idx else ""


def load_pil_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = resolve_font(name)
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# ── Data ────────────────────────────────────────────────────────────────

PROFILES_PATH = Path(__file__).parent.parent / "data" / "tapes.json"


def load_profiles() -> Dict:
    with open(PROFILES_PATH) as f:
        return json.load(f)

def load_templates() -> Dict:
    return {}


# ── Cell rendering ───────────────────────────────────────────────────────────

def _render_text_element(
    content: str,
    font_name: str,
    font_size: int,
    stretch_x: float,
    stretch_y: float,
    rotation: int,
    inner_w: int,
    inner_h: int,
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
) -> Image.Image:
    """Render text (possibly multiline) to an image sized to its content."""
    font = load_pil_font(font_name, font_size)

    # Measure with multiline support
    dummy = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.multiline_textbbox((0, 0), content, font=font, spacing=4)
    tw = max(bbox[2] - bbox[0], 1)
    th = max(bbox[3] - bbox[1], 1)

    # Render text onto a clean image
    txt_img = Image.new("RGB", (tw + 2, th + 2), bg)
    tdraw = ImageDraw.Draw(txt_img)
    tdraw.multiline_text((1 - bbox[0], 1 - bbox[1]), content, fill=fg, font=font, spacing=4)

    # Stretch
    if stretch_x != 1.0 or stretch_y != 1.0:
        nw = max(1, int(txt_img.width * stretch_x))
        nh = max(1, int(txt_img.height * stretch_y))
        txt_img = txt_img.resize((nw, nh), Image.LANCZOS)

    # Rotate
    if rotation:
        txt_img = txt_img.rotate(-rotation, expand=True, fillcolor=bg)

    # Scale down to fit inner area if too large
    if txt_img.width > inner_w or txt_img.height > inner_h:
        ratio = min(inner_w / txt_img.width, inner_h / txt_img.height)
        nw = max(1, int(txt_img.width * ratio))
        nh = max(1, int(txt_img.height * ratio))
        txt_img = txt_img.resize((nw, nh), Image.LANCZOS)

    return txt_img


def _render_qr_element(
    content: str,
    error_correction: str,
    inner_w: int,
    inner_h: int,
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
) -> Image.Image:
    import qrcode
    ec_map = {"L": qrcode.constants.ERROR_CORRECT_L,
               "M": qrcode.constants.ERROR_CORRECT_M,
               "Q": qrcode.constants.ERROR_CORRECT_Q,
               "H": qrcode.constants.ERROR_CORRECT_H}
    qr = qrcode.QRCode(
        error_correction=ec_map.get(error_correction, qrcode.constants.ERROR_CORRECT_M),
        border=1,
    )
    qr.add_data(content)
    qr.make(fit=True)
    size = min(inner_w, inner_h)
    img = qr.make_image(fill_color=fg, back_color=bg).convert("RGB")
    img = img.resize((size, size), Image.NEAREST)
    return img


def _render_barcode_element(
    content: str,
    fmt: str,
    inner_w: int,
    inner_h: int,
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
) -> Image.Image:
    try:
        import barcode
        from barcode.writer import ImageWriter
        bc_cls = barcode.get_barcode_class(fmt)
        writer = ImageWriter()
        buf = io.BytesIO()
        bc_cls(content, writer=writer).write(buf, options={
            "write_text": False,
            "foreground": "black",
            "background": "white",
        })
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
        img.thumbnail((inner_w, inner_h), Image.LANCZOS)
        return img
    except Exception as e:
        # Fallback: render error text
        font = load_pil_font("LiberationSans-Bold", 12)
        img = Image.new("RGB", (inner_w, inner_h), bg)
        ImageDraw.Draw(img).text((2, 2), f"[barcode error: {e}]", fill=fg, font=font)
        return img


def _render_image_element(
    image_data: str,
    inner_w: int,
    inner_h: int,
    invert: bool,
) -> Image.Image:
    raw = base64.b64decode(image_data)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if invert:
        from PIL import ImageOps
        img = ImageOps.invert(img)
    img.thumbnail((inner_w, inner_h), Image.LANCZOS)
    return img


def _place_element(
    cell_img: Image.Image,
    element: Image.Image,
    align_h: str,
    align_v: str,
    pad_l: int,
    pad_t: int,
    inner_w: int,
    inner_h: int,
    bg: Tuple[int, int, int],
) -> None:
    """Paste element onto cell_img with alignment."""
    ew, eh = element.size

    if align_h == "left":
        x = pad_l
    elif align_h == "right":
        x = pad_l + inner_w - ew
    else:
        x = pad_l + (inner_w - ew) // 2

    if align_v == "top":
        y = pad_t
    elif align_v == "bottom":
        y = pad_t + inner_h - eh
    else:
        y = pad_t + (inner_h - eh) // 2

    cell_img.paste(element, (x, y))


def render_cell(cell_spec: dict, width: int, height: int) -> Image.Image:
    """Render a single grid cell to a PIL image of (width, height)."""
    invert = cell_spec.get("invert", False)
    bg: Tuple[int, int, int] = (0, 0, 0) if invert else (255, 255, 255)
    fg: Tuple[int, int, int] = (255, 255, 255) if invert else (0, 0, 0)

    cell_img = Image.new("RGB", (width, height), bg)

    pad_t = cell_spec.get("padding_top", 2)
    pad_b = cell_spec.get("padding_bottom", 2)
    pad_l = cell_spec.get("padding_left", 4)
    pad_r = cell_spec.get("padding_right", 4)
    inner_w = max(1, width - pad_l - pad_r)
    inner_h = max(1, height - pad_t - pad_b)
    align_h = cell_spec.get("align_h", "center")
    align_v = cell_spec.get("align_v", "center")
    cell_type = cell_spec.get("type", "text")

    element: Optional[Image.Image] = None

    if cell_type == "text":
        content = cell_spec.get("content", "")
        if not content:
            return cell_img
        element = _render_text_element(
            content=content,
            font_name=cell_spec.get("font", "LiberationSans-Bold"),
            font_size=cell_spec.get("font_size", 60),
            stretch_x=cell_spec.get("stretch_x", 1.0),
            stretch_y=cell_spec.get("stretch_y", 1.0),
            rotation=cell_spec.get("rotation", 0),
            inner_w=inner_w,
            inner_h=inner_h,
            fg=fg,
            bg=bg,
        )

    elif cell_type == "qr":
        content = cell_spec.get("content", "")
        if not content:
            return cell_img
        element = _render_qr_element(
            content=content,
            error_correction=cell_spec.get("qr_error_correction", "M"),
            inner_w=inner_w,
            inner_h=inner_h,
            fg=fg,
            bg=bg,
        )

    elif cell_type == "barcode":
        content = cell_spec.get("content", "")
        if not content:
            return cell_img
        element = _render_barcode_element(
            content=content,
            fmt=cell_spec.get("barcode_format", "code128"),
            inner_w=inner_w,
            inner_h=inner_h,
            fg=fg,
            bg=bg,
        )

    elif cell_type == "image":
        image_data = cell_spec.get("image_data")
        if not image_data:
            return cell_img
        element = _render_image_element(image_data, inner_w, inner_h, invert)

    if element is not None:
        _place_element(cell_img, element, align_h, align_v, pad_l, pad_t, inner_w, inner_h, bg)

    return cell_img


# ── Natural width estimation ─────────────────────────────────────────────────

def _natural_cell_width(cell: dict, row_h: int) -> int:
    """Estimate natural pixel width of a cell given its row height."""
    pad_l = cell.get("padding_left", 4)
    pad_r = cell.get("padding_right", 4)
    inner_h = row_h - cell.get("padding_top", 2) - cell.get("padding_bottom", 2)
    cell_type = cell.get("type", "text")

    if cell_type == "text":
        content = cell.get("content", "")
        if not content:
            return pad_l + pad_r + 40
        font = load_pil_font(cell.get("font", "LiberationSans-Bold"), cell.get("font_size", 60))
        dummy = Image.new("RGB", (1, 1))
        bbox = ImageDraw.Draw(dummy).multiline_textbbox((0, 0), content, font=font, spacing=4)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tw = int(tw * cell.get("stretch_x", 1.0))
        th = int(th * cell.get("stretch_y", 1.0))
        rotation = cell.get("rotation", 0)
        if rotation in (90, 270):
            tw, th = th, tw
        # If text is taller than inner_h, it will be scaled down — adjust width accordingly
        if th > inner_h and th > 0:
            tw = int(tw * inner_h / th)
        return tw + pad_l + pad_r + 4

    elif cell_type in ("qr", "image"):
        return inner_h + pad_l + pad_r

    elif cell_type == "barcode":
        return inner_h * 3 + pad_l + pad_r  # barcodes are typically wider

    return 100


# ── Main render entry point ──────────────────────────────────────────────────

def render_label(spec: dict) -> Image.Image:
    """
    Render a full label from a LabelSpec dict.
    Returns a PIL Image of size (label_length, stripe_size).
    """
    profiles = load_profiles()
    profile_key = spec.get("tape_profile", "12mm_TZe")
    if profile_key not in profiles:
        profile_key = next(iter(profiles))
    profile = profiles[profile_key]
    stripe_size: int = profile["stripe_size"]
    # print_area: fraction of stripe height available for content (0.0–1.0).
    # The remainder becomes equal top/bottom padding, centering content on tape.
    # Tune per tape profile in tapes.json. Default 1.0 = full stripe.
    print_area: float = float(profile.get("print_area", 1.0))
    content_height: int = max(1, round(stripe_size * print_area))
    top_pad: int = (stripe_size - content_height) // 2

    rows: List[dict] = spec.get("rows", [])
    if not rows:
        return Image.new("RGB", (200, stripe_size), (255, 255, 255))

    # ── Row heights (within content_height, not full stripe_size) ────────────
    total_row_weight = sum(r.get("weight", 1.0) for r in rows)
    row_heights: List[int] = []
    remaining = content_height
    for i, row in enumerate(rows):
        if i == len(rows) - 1:
            h = remaining
        else:
            h = round(content_height * row.get("weight", 1.0) / total_row_weight)
            remaining -= h
        row_heights.append(max(1, h))

    # ── Natural cell widths per row ──────────────────────────────────────────
    row_cell_widths: List[List[int]] = []
    for row, row_h in zip(rows, row_heights):
        cells = row.get("cells", [])
        row_cell_widths.append([_natural_cell_width(c, row_h) for c in cells])

    # ── Label width = widest row ─────────────────────────────────────────────
    row_natural_totals = [sum(ws) for ws in row_cell_widths]
    label_width = max(
        max(row_natural_totals) if row_natural_totals else 100,
        spec.get("min_label_width", 0),
        50,
    )

    # ── Compose label image ──────────────────────────────────────────────────
    label = Image.new("RGB", (label_width, stripe_size), (255, 255, 255))

    y_offset = top_pad
    for row, row_h, natural_widths in zip(rows, row_heights, row_cell_widths):
        cells = row.get("cells", [])
        if not cells:
            y_offset += row_h
            continue

        row_total = sum(natural_widths)

        # Distribute label_width among cells using their weight field,
        # but anchor to natural sizes so QR codes don't distort.
        # Strategy: cells get min(natural_width, weight_share).
        total_weight = sum(c.get("weight", 1.0) for c in cells)
        x_offset = 0

        for i, (cell, natural_w) in enumerate(zip(cells, natural_widths)):
            if i == len(cells) - 1:
                # Last cell absorbs rounding
                cell_w = label_width - x_offset
            else:
                weight_share = round(label_width * cell.get("weight", 1.0) / total_weight)
                cell_w = weight_share
            cell_w = max(1, cell_w)

            cell_img = render_cell(cell, cell_w, row_h)
            label.paste(cell_img, (x_offset, y_offset))
            x_offset += cell_w

        y_offset += row_h

    # ── Whole-label rotation ─────────────────────────────────────────────────
    label_rotation = spec.get("label_rotation", 0)
    if label_rotation:
        label = label.rotate(-label_rotation, expand=True, fillcolor=(255, 255, 255))

    return label


def label_to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
