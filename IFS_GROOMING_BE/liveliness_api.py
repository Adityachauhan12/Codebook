# liveliness_api.py
"""
FastAPI server for:
- Liveliness (streaming frames) -> returns captured success photo + logs to GCS
- Grooming check from an image -> stores raw + structured results to GCS
- Analytics & Tables (INLINED) -> reads GCS results and returns KPIs & table rows
"""

from __future__ import annotations

import os
import re
import json
import base64
import shutil
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple

from dotenv import load_dotenv
load_dotenv()  # ensure .env is loaded before imports use env vars

from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# --- Project imports
import config
from grooming_utils import (
    check_grooming,                    # backward-compat single-image call
    check_grooming_from_video,        # legacy video flow
)
# Prefer structured helper if you added it earlier; if not present, we add a local wrapper below.
try:
    from grooming_utils import assess_image_return_structured, parse_grooming_text  # type: ignore
    HAS_ASSESS_IMAGE_STRUCTURED = True
except Exception:
    HAS_ASSESS_IMAGE_STRUCTURED = False
    # Fallback lightweight parser (best-effort) if not available in grooming_utils
    import re as _re
    _PARSE_MAP = {
        "overall": _re.compile(r"^Overall\s+Assessment:\s*(.+)$", _re.I | _re.M),
        "score":   _re.compile(r"^Overall\s+Score:\s*([0-9]+(?:\.[0-9]+)?)", _re.I | _re.M),
        "hair":    _re.compile(r"^-+\s*Hairstyle:\s*(.+)$", _re.I | _re.M),
        "makeup":  _re.compile(r"^-+\s*Makeup:\s*(.+)$", _re.I | _re.M),
        "nails":   _re.compile(r"^-+\s*Nails:\s*(.+)$", _re.I | _re.M),
        "acc":     _re.compile(r"^-+\s*Accessories:\s*(.+)$", _re.I | _re.M),
        "uniform": _re.compile(r"^-+\s*Uniform:\s*(.+)$", _re.I | _re.M),
        "issues":  _re.compile(r"^Issues\s*Found:\s*(.+)$", _re.I | _re.M | _re.S),
        "reco":    _re.compile(r"^Recommendations:\s*(.+)$", _re.I | _re.M | _re.S),
    }
    def parse_grooming_text(text: str) -> dict:
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
    # Minimal structured wrapper
    def assess_image_return_structured(image_b64: str) -> Dict[str, Any]:
        full_text = check_grooming(image_b64)
        return {
            "full_text": full_text,
            "parsed": parse_grooming_text(full_text),
            "model_meta": {"endpoint": config.GEMINI_ENDPOINT or "built_from_env", "model": getattr(config, "GEMINI_MODEL", "")},
        }

from liveliness_functionality import (
    IndigoGroomingAssessment,
    StreamingLiveliness,
    decode_frame_b64,
)

# GCS helpers
from gcs_utils import (
    upload_image_bytes,
    append_event_to_crew_log,
    create_ticket,
    latest_assessments_today,
    upload_grooming_result_text,
    GCS_BUCKET_NAME, GCS_BASE_FOLDER
)

# Optional: if you added the structured uploader earlier
try:
    from gcs_utils import upload_grooming_result_structured  # type: ignore
    HAS_STRUCTURED_UPLOADER = True
except Exception:
    HAS_STRUCTURED_UPLOADER = False


# ------------------------------------------------------------------------------
# FastAPI app + CORS
# ------------------------------------------------------------------------------
app = FastAPI(title="IFS Grooming & Liveliness API", version="1.0.0")

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

class LivelinessFrameRequest(BaseModel):
    sessionId: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None
    frameBase64: str  # raw base64 jpeg (no data: prefix)

# ------------------------------------------------------------------------------
# Utils
# ------------------------------------------------------------------------------
def _slugify_iga(iga_code: Optional[str]) -> str:
    s = (iga_code or "Unknown").strip()
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", s)
    return s or "Unknown"

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _save_success_photo_locally(img_bytes: bytes, iga_code: Optional[str]) -> str:
    iga = _slugify_iga(iga_code)
    date_dir = datetime.now().strftime("%Y%m%d")
    out_dir = os.path.join("recording", "streams", iga, date_dir)
    _ensure_dir(out_dir)
    filename = f"photo_{datetime.now().strftime('%H%M%S')}.jpg"
    out_path = os.path.join(out_dir, filename)
    with open(out_path, "wb") as f:
        f.write(img_bytes)
    return out_path

# ------------------------------------------------------------------------------
# Health & session maintenance
# ------------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": datetime.now().isoformat()}

assessor = IndigoGroomingAssessment()
streaming_assessor = StreamingLiveliness() # Using the new improved class

@app.post("/session/end")
async def end_session(sessionId: str):
    streaming_assessor.end_session(sessionId)
    return {"status": "ended", "sessionId": sessionId}

@app.post("/session/cleanup")
async def cleanup_sessions():
    # This is a placeholder for a potential cleanup method you might add
    # to StreamingLiveliness to remove old, expired sessions.
    # removed = streaming_assessor.cleanup_expired()
    # return {"removed_sessions": removed}
    return {"status": "cleanup endpoint is a placeholder"}


# ------------------------------------------------------------------------------
# Liveliness (streaming frames)
# ------------------------------------------------------------------------------
@app.post("/liveliness-frame")
async def liveliness_frame(payload: LivelinessFrameRequest):
    try:
        frame = decode_frame_b64(payload.frameBase64)
        if frame is None:
            return JSONResponse(status_code=400, content={"event": "error", "message": "Invalid image frame"})
        
        result = streaming_assessor.process_frame(
            session_id=payload.sessionId,
            frame_bgr=frame,
            crew_name=payload.crewName or "Unknown",
            iga_code=payload.igaCode or "Unknown",
        )
        
        if result["event"] == "success":
            img_bytes = result.get("captured_frame_bytes")
            if not img_bytes:
                return JSONResponse(status_code=500, content={"event": "error", "message": "Failed to capture success frame."})

            gcs_image_path = upload_image_bytes(
                img_bytes, iga_code=(payload.igaCode or "Unknown"),
                crew_name=payload.crewName, kind="frame"
            )
            event_item = {
                "type": "liveliness_frame",
                "liveliness_status": "LIVE",
                "image_gcs_path": gcs_image_path,
                "session_id": payload.sessionId,
            }
            _, _ = append_event_to_crew_log(event_item, crew_name=payload.crewName, iga_code=payload.igaCode)
            ticket_id = create_ticket({"event": "liveliness_frame", "iga_code": payload.igaCode, "crew_name": payload.crewName, **event_item})
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            streaming_assessor.end_session(payload.sessionId)
            return {
                "event": "success",
                "captured_frame_b64": img_b64,
                "gcs_image_path": gcs_image_path,
                "ticket_id": ticket_id,
            }

        elif result["event"] == "progress":
            return result # The result already has the correct format {"event": "progress", "actions_done": ...}
            
        else: # Handle other cases like errors
            return JSONResponse(status_code=200, content=result)
            
    except Exception as e:
        print(f"X Error in /liveliness-frame: {str(e)}")
        return JSONResponse(status_code=500, content={"event": "error", "message": str(e)})

# ------------------------------------------------------------------------------
# Grooming from captured image (structured result)
# ------------------------------------------------------------------------------
@app.post("/check-grooming")
async def check_grooming_endpoint(payload: GroomingRequest):
    try:
        base64_image = payload.imageBase64
        if not base64_image or not isinstance(base64_image, str):
            return JSONResponse(status_code=400, content={"error": "Invalid image data"})

        clean_b64 = base64_image.split(',')[-1]
        img_bytes = base64.b64decode(clean_b64)

        structured = assess_image_return_structured(clean_b64)

        image_gcs_path = upload_image_bytes(
            img_bytes,
            iga_code=(payload.igaCode or "Unknown"),
            crew_name=payload.crewName,
            kind="grooming_image"
        )

        upload_grooming_result_text(
            result_text=structured["full_text"],
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
            image_gcs_path=image_gcs_path
        )

        event_item = {
            "type": "grooming_image",
            "parsed": structured["parsed"] if any(structured["parsed"].values()) else None
        }
        append_event_to_crew_log(event_item, crew_name=payload.crewName, iga_code=payload.igaCode)
        ticket_id = create_ticket({
            "event": "grooming_image",
            "iga_code": payload.igaCode,
            "crew_name": payload.crewName,
            **event_item
        })

        # **FIX**: Return the full, raw text from the AI to ensure correct formatting.
        display_text = structured["full_text"]

        return {
            "display_text": display_text,
            "parsed": structured["parsed"],
            "ticket_id": ticket_id,
        }
    except Exception as e:
        print(f"X Error in /check-grooming: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ------------------------------------------------------------------------------
# Legacy video routes (optional, kept as-is)
# ------------------------------------------------------------------------------
@app.post("/liveliness-from-video")
async def liveliness_from_video(
    video: UploadFile = File(...),
    crewName: str = Form(...),
    igaCode: str = Form(...)
):
    try:
        video_dir = "recording"
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, video.filename)
        with open(video_path, "wb") as buffer:
            buffer.write(await video.read())
        result = assessor.detect_liveliness_and_capture_frame(video_path)
        image_base64 = ""
        if result.get("captured_frame_path") and os.path.exists(result["captured_frame_path"]):
            with open(result["captured_frame_path"], "rb") as img_file:
                image_base64 = base64.b64encode(img_file.read()).decode("utf-8")
        return {
            "crewName": crewName,
            "igaCode": igaCode,
            "liveliness_status": result.get("liveliness_status"),
            "blink_count": result.get("blink_count"),
            "liveliness_score": result.get("liveliness_score"),
            "imageBase64": image_base64,
            "video_path": result.get("video_path"),
        }
    except Exception as e:
        print(f"X Error during liveliness check: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/check-grooming-video")
async def check_grooming_video_endpoint(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...)
):
    try:
        from google.cloud import storage
        # Save video locally
        video_dir = "uploads/videos"
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, video.filename)
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # Run Gemini grooming check (note: may hit size limits on large videos)
        result_text = check_grooming_from_video(video_path, name, iga_code)

        # Upload video to GCS under dedicated folder
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
        video_blob = bucket.blob(video_blob_path)
        video_blob.metadata = {"crew_name": name, "iga_code": iga_code, "type": "grooming_video"}
        video_blob.upload_from_string(video_bytes, content_type="video/mp4")

        # Upload grooming result JSON
        result_payload = {
            "timestamp": dt.isoformat(),
            "iga_code": iga_code,
            "crew_name": name,
            "video_gcs_path": f"gs://{bucket.name}/{video_blob_path}",
            "result_text": result_text
        }
        result_blob = bucket.blob(result_blob_path)
        result_blob.metadata = {"crew_name": name, "iga_code": iga_code, "type": "grooming_result"}
        result_blob.upload_from_string(
            data=json.dumps(result_payload, indent=2),
            content_type="application/json"
        )

        # Log and ticket
        event_item = {
            "type": "grooming_video",
            "result_text": result_text,
            "video_gcs_path": f"gs://{bucket.name}/{video_blob_path}",
            "result_gcs_path": f"gs://{bucket.name}/{result_blob_path}"
        }
        _, _ = append_event_to_crew_log(event_item, crew_name=name, iga_code=iga_code)
        ticket_id = create_ticket({"event": "grooming_video", "iga_code": iga_code, "crew_name": name, **event_item})
        today_summary = latest_assessments_today(iga_code)

        return {
            "result": result_text,
            "video_gcs_path": f"gs://{bucket.name}/{video_blob_path}",
            "result_gcs_path": f"gs://{bucket.name}/{result_blob_path}",
            "ticket_id": ticket_id,
            "today_summary": today_summary
        }
    except Exception as e:
        print(f"X Error in /check-grooming-video: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ------------------------------------------------------------------------------
# Main (dev)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("liveliness_api:app", host="0.0.0.0", port=config.PORT, reload=True)
