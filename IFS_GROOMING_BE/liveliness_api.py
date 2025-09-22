# liveliness_api.py (image + video; detailed category scores + clean UI object)

from __future__ import annotations
import os, re, json, base64, shutil
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

import config  # has PORT etc.

# Grooming core
from grooming_utils import check_grooming, check_grooming_from_video  # text outputs

# Optional structured helper
try:
    from grooming_utils import assess_image_return_structured, parse_grooming_text  # type: ignore
    HAS_ASSESS_IMAGE_STRUCTURED = True
except Exception:
    HAS_ASSESS_IMAGE_STRUCTURED = False

# ---------------- Parsing helpers ----------------
_rx = {
    "overall_assessment": re.compile(r"^Overall\s*Assessment\s*:\s*(.+)$", re.I | re.M),
    "overall_score":     re.compile(r"Overall\s*Score\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10", re.I),
    "uniform_score":     re.compile(r"Uniform\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*3", re.I),
    "nails_score":       re.compile(r"Nails\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*1", re.I),
    "hairstyle_score":   re.compile(r"Hairstyle\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "makeup_score":      re.compile(r"Makeup\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "accessories_score": re.compile(r"Accessories\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "hair_detail":       re.compile(r"-\s*Hairstyle\s*:\s*(.+)$", re.I | re.M),
    "makeup_detail":     re.compile(r"-\s*Makeup\s*:\s*(.+)$", re.I | re.M),
    "nails_detail":      re.compile(r"-\s*Nails\s*:\s*(.+)$", re.I | re.M),
    "acc_detail":        re.compile(r"-\s*Accessories\s*:\s*(.+)$", re.I | re.M),
    "uniform_detail":    re.compile(r"-\s*Uniform\s*:\s*(.+)$", re.I | re.M),
    "issues_block":      re.compile(r"Issues\s*Found\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
    "reco_block":        re.compile(r"Recommendations\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
}

def _extract(m: re.Pattern, text: str, default: str = "") -> str:
    hit = m.search(text or "")
    return hit.group(1).strip() if hit else default

def _num(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except Exception:
        return default

def _lines_to_list(block: str) -> list[str]:
    out = []
    for ln in (block or "").splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.startswith("-"):
            out.append(t.lstrip("- ").strip())
        elif t[0:2].isdigit() or t.startswith(("1.", "2.", "3.", "4.", "5.")):
            out.append(t.split(".", 1)[-1].strip() if "." in t else t)
    return out

def _parse_text_to_ui(full_text: str) -> Dict[str, Any]:
    # Optional project parser
    parsed_details = {}
    try:
        from grooming_utils import parse_grooming_text  # type: ignore
        parsed_details = parse_grooming_text(full_text) or {}
    except Exception:
        parsed_details = {}

    # Robust fallback extraction
    assessment = _extract(_rx["overall_assessment"], full_text, parsed_details.get("overall_assessment", "UNKNOWN"))
    score_overall = _num(_extract(_rx["overall_score"], full_text, f'{parsed_details.get("overall_score","0")}'))

    details = {
        "uniform":   _extract(_rx["uniform_detail"], full_text, (parsed_details.get("details") or {}).get("uniform","")),
        "hairstyle": _extract(_rx["hair_detail"], full_text, (parsed_details.get("details") or {}).get("hairstyle","")),
        "makeup":    _extract(_rx["makeup_detail"], full_text, (parsed_details.get("details") or {}).get("makeup","")),
        "nails":     _extract(_rx["nails_detail"], full_text, (parsed_details.get("details") or {}).get("nails","")),
        "accessories": _extract(_rx["acc_detail"], full_text, (
