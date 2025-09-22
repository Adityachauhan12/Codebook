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
        "accessories": _extract(_rx["acc_detail"], full_text, (parsed_details.get("details") or {}).get("accessories","")),
    }

    scores = {
        "uniform":     _num(_extract(_rx["uniform_score"], full_text)),
        "nails":       _num(_extract(_rx["nails_score"], full_text)),
        "hairstyle":   _num(_extract(_rx["hairstyle_score"], full_text)),
        "makeup":      _num(_extract(_rx["makeup_score"], full_text)),
        "accessories": _num(_extract(_rx["accessories_score"], full_text)),
    }

    issues = _lines_to_list(_extract(_rx["issues_block"], full_text))
    recos  = _lines_to_list(_extract(_rx["reco_block"], full_text))

    return {
        "assessment": assessment or "UNKNOWN",
        "score": score_overall,
        "scores": scores,
        "details": details,
        "issues": issues,
        "recommendations": recos,
    }

# Optional “assess image and also return text”
def _assess_image_to_text(image_b64: str) -> str:
    if HAS_ASSESS_IMAGE_STRUCTURED:
        try:
            return assess_image_return_structured(image_b64)["full_text"]  # type: ignore
        except Exception:
            pass
    return check_grooming(image_b64)

# GCS helpers
from gcs_utils import (
    upload_image_bytes,
    upload_grooming_result_text,
    append_event_to_crew_log,
    create_ticket,
    GCS_BUCKET_NAME,  # noqa
    GCS_BASE_FOLDER,  # noqa
)

# --------------- FastAPI setup ---------------
app = FastAPI(title="IFS Grooming API (detailed)", version="2.2.0")

ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOW_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GroomingRequest(BaseModel):
    imageBase64: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": datetime.now().isoformat()}

# ---------- Image endpoint ----------
@app.post("/check-grooming")
async def check_grooming_endpoint(payload: GroomingRequest):
    try:
        b64 = payload.imageBase64 or ""
        if not isinstance(b64, str) or len(b64) < 10:
            return JSONResponse(status_code=400, content={"error": "Invalid image data"})
        clean_b64 = b64.split(",")[-1]
        img_bytes = base64.b64decode(clean_b64)

        full_text = _assess_image_to_text(clean_b64)
        ui_result = _parse_text_to_ui(full_text)

        image_gcs_path = upload_image_bytes(
            img_bytes,
            iga_code=(payload.igaCode or "Unknown"),
            crew_name=payload.crewName,
            kind="grooming_image",
        )
        upload_grooming_result_text(
            result_text=full_text,
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
            image_gcs_path=image_gcs_path,
        )
        append_event_to_crew_log(
            {"type": "grooming_image", "parsed": ui_result, "image_gcs_path": image_gcs_path},
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
        )
        _ = create_ticket({"event": "grooming_image", "iga_code": payload.igaCode, "crew_name": payload.crewName})

        return {"status": "ok", "result": ui_result}
    except Exception as e:
        print(f"X Error in /check-grooming: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ---------- Video endpoint ----------
@app.post("/check-grooming-video")
async def check_grooming_video_endpoint(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...),
):
    try:
        video_dir = "uploads/videos"
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, video.filename)
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # Returns a long text (includes scores block)
        full_text = check_grooming_from_video(video_path, name, iga_code)
        ui_result = _parse_text_to_ui(full_text)

        # Persist text only (video upload optional in your project)
        upload_grooming_result_text(
            result_text=full_text,
            crew_name=name,
            iga_code=iga_code,
            image_gcs_path=None,
        )
        append_event_to_crew_log({"type": "grooming_video", "parsed": ui_result}, crew_name=name, iga_code=iga_code)
        _ = create_ticket({"event": "grooming_video", "iga_code": iga_code, "crew_name": name})

        return {"status": "ok", "result": ui_result}
    except Exception as e:
        print(f"X Error in /check-grooming-video: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("liveliness_api:app", host="0.0.0.0", port=config.PORT, reload=True)
