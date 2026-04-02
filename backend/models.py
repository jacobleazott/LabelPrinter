from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class LabelCell(BaseModel):
    type: Literal["text", "qr", "barcode", "image"] = "text"
    content: str = ""

    # Text
    font: str = "LiberationSans-Bold"
    font_size: int = Field(default=60, ge=6, le=600)
    stretch_x: float = Field(default=1.0, ge=0.1, le=8.0)
    stretch_y: float = Field(default=1.0, ge=0.1, le=8.0)
    invert: bool = False  # white text on black background

    # Layout within cell
    rotation: Literal[0, 90, 180, 270] = 0
    align_h: Literal["left", "center", "right"] = "center"
    align_v: Literal["top", "center", "bottom"] = "center"
    padding_top: int = Field(default=2, ge=0, le=200)
    padding_bottom: int = Field(default=2, ge=0, le=200)
    padding_left: int = Field(default=4, ge=0, le=200)
    padding_right: int = Field(default=4, ge=0, le=200)

    # Relative width within its row (like CSS flex-grow)
    weight: float = Field(default=1.0, ge=0.1, le=20.0)

    # QR / barcode options
    qr_error_correction: Literal["L", "M", "Q", "H"] = "M"
    barcode_format: str = "code128"

    # Image: base64-encoded PNG/JPEG
    image_data: Optional[str] = None


class LabelRow(BaseModel):
    cells: List[LabelCell] = Field(default_factory=list)
    # Relative height within the tape stripe (like CSS flex-grow)
    weight: float = Field(default=1.0, ge=0.1, le=20.0)


class LabelSpec(BaseModel):
    tape_profile: str = "12mm_TZe"
    rows: List[LabelRow] = Field(default_factory=list)
    copies: int = Field(default=1, ge=1, le=999)
    cut_mode: Literal["full", "half", "none"] = "full"
    # Rotate the entire finished label (useful for cable/patch labels)
    label_rotation: Literal[0, 90, 180, 270] = 0
    # Minimum label length in pixels (pad if shorter)
    min_label_width: int = Field(default=0, ge=0)
    top_margin: int = Field(default=8, ge=0, le=200)
    bottom_margin: int = Field(default=8, ge=0, le=200)


class BatchPrintRequest(BaseModel):
    template: LabelSpec
    # Each item replaces the content of the first text cell in the template
    items: List[str]
