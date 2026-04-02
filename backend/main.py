"""
Label Printer — FastAPI backend
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import BatchPrintRequest, LabelSpec
from .renderer import font_index, label_to_png_bytes, load_profiles, render_label
from .printer import get_printer_status, print_label

PRINTER_IP = os.environ.get("PRINTER_IP", "192.168.10.110")
PRINTER_PORT = int(os.environ.get("PRINTER_PORT", "9100"))

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Label Printer", version="1.0.0")

# ── Serve frontend ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = FRONTEND_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(), status_code=200)


# ── Profiles & fonts ──────────────────────────────────────────────────────────

@app.get("/api/profiles")
async def get_profiles():
    return load_profiles()

@app.get("/api/templates")
async def get_templates():
    return load_templates()

@app.get("/api/fonts")
async def get_fonts():
    idx = font_index()
    return [{"name": name, "path": path} for name, path in idx.items()]


# ── Preview ───────────────────────────────────────────────────────────────────

@app.post("/api/preview")
async def preview(spec: LabelSpec):
    try:
        img = render_label(spec.model_dump())
        png = label_to_png_bytes(img)
        return Response(content=png, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Print ─────────────────────────────────────────────────────────────────────

@app.post("/api/print")
async def print_endpoint(spec: LabelSpec):
    profiles = load_profiles()
    profile_key = spec.tape_profile
    if profile_key not in profiles:
        raise HTTPException(status_code=400, detail=f"Unknown tape profile: {profile_key}")
    profile = profiles[profile_key]

    try:
        img = render_label(spec.model_dump())
        print_label(
            img=img,
            stripe_size=profile["stripe_size"],
            media_width_mm=profile["width_mm"],
            ip=PRINTER_IP,
            port=PRINTER_PORT,
            top_margin=spec.top_margin,
            bottom_margin=spec.bottom_margin,
            cut_mode=spec.cut_mode,
            copies=spec.copies,
            y_offset=profile.get("y_offset", 0),
        )
        return {"ok": True, "message": f"Sent {spec.copies} label(s) to {PRINTER_IP}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Batch print ───────────────────────────────────────────────────────────────

@app.post("/api/batch")
async def batch_print(req: BatchPrintRequest):
    profiles = load_profiles()
    profile_key = req.template.tape_profile
    if profile_key not in profiles:
        raise HTTPException(status_code=400, detail=f"Unknown tape profile: {profile_key}")
    profile = profiles[profile_key]

    errors = []
    sent = 0

    for item_text in req.items:
        # Deep-copy template and inject text into first text cell found
        spec_dict = req.template.model_dump()
        injected = False
        for row in spec_dict.get("rows", []):
            for cell in row.get("cells", []):
                if cell.get("type") == "text":
                    cell["content"] = item_text
                    injected = True
                    break
            if injected:
                break

        try:
            img = render_label(spec_dict)
            print_label(
                img=img,
                stripe_size=profile["stripe_size"],
                media_width_mm=profile["width_mm"],
                ip=PRINTER_IP,
                port=PRINTER_PORT,
                top_margin=req.template.top_margin,
                bottom_margin=req.template.bottom_margin,
                cut_mode=req.template.cut_mode,
                copies=1,
                y_offset=profile.get("y_offset", 0),
            )
            sent += 1
        except Exception as e:
            errors.append({"item": item_text, "error": str(e)})

    return {"sent": sent, "errors": errors}


# ── Image upload ──────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    import base64
    content = await file.read()
    b64 = base64.b64encode(content).decode()
    return {"image_data": b64, "filename": file.filename}


# ── Printer status ────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    return get_printer_status(PRINTER_IP)


@app.get("/api/printer-url")
async def printer_url():
    return {"url": f"http://{PRINTER_IP}/printer/main.html", "ip": PRINTER_IP}



# app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
app.mount("/", StaticFiles(directory="/app/frontend", html=True), name="frontend")