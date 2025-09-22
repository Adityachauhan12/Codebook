# liveliness_api.py (grooming-only + video endpoint)

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

# Project config and utils (existing in your project)
import config  # expects PORT and any other env-backed config

# Grooming helpers
from grooming_utils import check_grooming, check_grooming_from_video  # text outputs

# Try to import structured helpers if present
try:
    from grooming_utils import assess_image_return_structured, parse_grooming_text  # type: ignore
    HAS_ASSESS_IMAGE_STRUCTURED = True
except Exception:
    HAS_ASSESS_IMAGE_STRUCTURED = False

# Fallback lightweight parser if parse_grooming_text is not available
import re as _re
_PARSE_MAP = {
    "overall": _re.compile(r"^Overall\s+Assessment:\s*(.+)$", _re.I | _re.M),
    "score": _re.compile(r"^Overall\s+Score:\s*([0-9]+(?:\.[0-9]+)?)", _re.I | _re.M),
    "hair": _re.compile(r"^-+\s*Hairstyle:\s*(.+)$", _re.I | _re.M),
    "makeup": _re.compile(r"^-+\s*Makeup:\s*(.+)$", _re.I | _re.M),
    "nails": _re.compile(r"^-+\s*Nails:\s*(.+)$", _re.I | _re.M),
    "acc": _re.compile(r"^-+\s*Accessories:\s*(.+)$", _re.I | _re.M),
    "uniform": _re.compile(r"^-+\s*Uniform:\s*(.+)$", _re.I | _re.M),
    "issues": _re.compile(r"^Issues\s*Found:\s*(.+)$", _re.I | _re.M | _re.S),
    "reco": _re.compile(r"^Recommendations:\s*(.+)$", _re.I | _re.M | _re.S),
}
def _fallback_parse(text: str) -> dict:
    def _m(key):
        m = _PARSE_MAP[key].search(text or "")
        return m.group(1).strip() if m else ""
    return {
        "overall_assessment": _m("overall"),
        "overall_score": _m("score"),
        "details": {
            "hairstyle": _m("hair"),
            "makeup": _m("makeup"),
            "nails": _m("nails"),
            "accessories": _m("acc"),
            "uniform": _m("uniform"),
        },
        "issues_found": _m("issues"),
        "recommendations": _m("reco"),
    }

def _assess_structured(image_b64: str) -> Dict[str, Any]:
    if HAS_ASSESS_IMAGE_STRUCTURED:
        return assess_image_return_structured(image_b64)  # type: ignore
    full_text = check_grooming(image_b64)
    return {"full_text": full_text, "parsed": _fallback_parse(full_text), "model_meta": {}}

def _parse_text_to_ui(text: str) -> Dict[str, Any]:
    # Use project parser if available, else fallback
    parsed = None
    try:
        parsed = parse_grooming_text(text)  # type: ignore
    except Exception:
        parsed = _fallback_parse(text)

    def _lines_to_list(s: str) -> list[str]:
        out = []
        for ln in (s or "").splitlines():
            t = ln.strip()
            if not t:
                continue
            if t.startswith("-"):
                out.append(t.lstrip("- ").strip())
            elif t[0:2].isdigit() or t.startswith(("1.", "2.", "3.", "4.", "5.")):
                out.append(t.split(".", 1)[-1].strip() if "." in t else t)
        return out

    details = parsed.get("details", {}) or {}
    return {
        "assessment": parsed.get("overall_assessment") or "UNKNOWN",
        "score": float(parsed.get("overall_score") or 0.0),
        "details": {
            "uniform": details.get("uniform", ""),
            "hairstyle": details.get("hairstyle", ""),
            "makeup": details.get("makeup", ""),
            "nails": details.get("nails", ""),
            "accessories": details.get("accessories", ""),
        },
        "issues": _lines_to_list(parsed.get("issues_found", "")),
        "recommendations": _lines_to_list(parsed.get("recommendations", "")),
    }

# GCS helpers (already in your project)
from gcs_utils import (
    upload_image_bytes,
    upload_grooming_result_text,
    append_event_to_crew_log,
    create_ticket,
    GCS_BUCKET_NAME,  # noqa: F401
    GCS_BASE_FOLDER,  # noqa: F401
)

# ------------------------------------------------------------------------------
# FastAPI app + CORS
# ------------------------------------------------------------------------------
app = FastAPI(title="IFS Grooming API (liveness on FE)", version="2.1.0")

ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOW_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------
class GroomingRequest(BaseModel):
    imageBase64: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None

# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": datetime.now().isoformat()}

# ------------------------------------------------------------------------------
# Grooming from a single image (frontend sends after liveness pass)
# ------------------------------------------------------------------------------
@app.post("/check-grooming")
async def check_grooming_endpoint(payload: GroomingRequest):
    try:
        b64 = payload.imageBase64 or ""
        if not isinstance(b64, str) or len(b64) < 10:
            return JSONResponse(status_code=400, content={"error": "Invalid image data"})

        clean_b64 = b64.split(",")[-1]
        img_bytes = base64.b64decode(clean_b64)

        # Run grooming and normalize to UI shape
        structured = _assess_structured(clean_b64)
        ui_result = _parse_text_to_ui(structured["full_text"])

        # Store for audit, but do NOT expose internal ids/paths in response
        image_gcs_path = upload_image_bytes(
            img_bytes,
            iga_code=(payload.igaCode or "Unknown"),
            crew_name=payload.crewName,
            kind="grooming_image",
        )
        upload_grooming_result_text(
            result_text=structured["full_text"],
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
            image_gcs_path=image_gcs_path,
        )
        append_event_to_crew_log(
            {"type": "grooming_image", "parsed": ui_result, "image_gcs_path": image_gcs_path},
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
        )
        _ = create_ticket({"event": "grooming_image", "iga_code": payload.igaCode, "crew_name": payload.crewName, "image_gcs_path": image_gcs_path})

        return {"status": "ok", "result": ui_result}
    except Exception as e:
        print(f"X Error in /check-grooming: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ------------------------------------------------------------------------------
# Grooming from an uploaded video (not liveness)
# ------------------------------------------------------------------------------
@app.post("/check-grooming-video")
async def check_grooming_video_endpoint(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...),
):
    try:
        # Save video locally
        video_dir = "uploads/videos"
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, video.filename)
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # Run grooming on video (returns text)
        result_text = check_grooming_from_video(video_path, name, iga_code)

        # Try to upload video and result to GCS for audit
        video_gcs_path = None
        result_gcs_path = None
        try:
            from google.cloud import storage  # import here to avoid hard dependency at startup
            with open(video_path, "rb") as f:
                video_bytes = f.read()

            dt = datetime.now()
            date_str = dt.strftime("%Y%m%d")
            time_str = dt.strftime("%H%M%S")
            folder_path = f"{GCS_BASE_FOLDER}/{date_str}/uploaded_videos/{iga_code}"
            video_blob_path = f"{folder_path}/video_{time_str}.mp4"
            result_blob_path = f"{folder_path}/grooming_result_{iga_code}_{time_str}.json"

            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET_NAME)

            # Upload video
            vblob = bucket.blob(video_blob_path)
            vblob.metadata = {"crew_name": name, "iga_code": iga_code, "type": "grooming_video"}
            vblob.upload_from_string(video_bytes, content_type="video/mp4")
            video_gcs_path = f"gs://{bucket.name}/{video_blob_path}"

            # Upload result JSON
            rblob = bucket.blob(result_blob_path)
            rblob.metadata = {"crew_name": name, "iga_code": iga_code, "type": "grooming_result"}
            rblob.upload_from_string(
                data=json.dumps(
                    {"timestamp": dt.isoformat(), "iga_code": iga_code, "crew_name": name, "video_gcs_path": video_gcs_path, "result_text": result_text},
                    indent=2,
                ),
                content_type="application/json",
            )
            result_gcs_path = f"gs://{bucket.name}/{result_blob_path}"
        except Exception as gcs_err:
            print(f"Warn: GCS upload skipped or failed: {gcs_err}")

        # Normalize to UI shape and log event
        ui_result = _parse_text_to_ui(result_text)
        event_item = {"type": "grooming_video", "parsed": ui_result, "video_gcs_path": video_gcs_path, "result_gcs_path": result_gcs_path}
        append_event_to_crew_log(event_item, crew_name=name, iga_code=iga_code)
        _ = create_ticket({"event": "grooming_video", "iga_code": iga_code, "crew_name": name, **event_item})

        # Return clean response (no internal IDs/paths)
        return {"status": "ok", "result": ui_result}
    except Exception as e:
        print(f"X Error in /check-grooming-video: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ------------------------------------------------------------------------------
# Main (dev)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("liveliness_api:app", host="0.0.0.0", port=config.PORT, reload=True)
