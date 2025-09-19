"""
FastAPI server for liveliness detection (frame-based HTTP streaming) and grooming checks.
On SUCCESS (blink + head-turn):
  - Save success photo locally
  - Upload success photo to GCS
  - Append 'liveliness_frame' event to per-crew JSON on GCS
  - Append a ticket row to GCS tickets log
On grooming:
  - Upload input image to GCS
  - Save a result JSON named with IGA code under results/<IGA>/grooming_result_<IGA>_<HHMMSS>.json
  - Append to per-crew log and tickets
"""
import os
import re
import base64
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import PORT
from grooming_utils import check_grooming, check_grooming_from_video
from liveliness_functionality import (
    IndigoGroomingAssessment,
    StreamingLiveliness,
    decode_frame_b64,
)

# --- GCS helpers
from gcs_utils import (
    upload_image_bytes,
    append_event_to_crew_log,
    create_ticket,
    latest_assessments_today,
    upload_grooming_result_text,
)

load_dotenv()

app = FastAPI()
assessor = IndigoGroomingAssessment()

# Streaming assessor (frame method)
streaming_assessor = StreamingLiveliness(
    require_head_turn=True,
    ear_drop_ratio=0.28,
    ema_alpha=0.15,
    min_closed_frames=1,
    nose_dx_threshold=0.02
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GroomingRequest(BaseModel):
    imageBase64: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None

class LivelinessFrameRequest(BaseModel):
    sessionId: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None
    frameBase64: str  # raw base64 jpeg (no data: prefix)

def _slugify_iga(iga_code: Optional[str]) -> str:
    s = (iga_code or "Unknown").strip()
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s)
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
            # 1) Save locally (as before)
            saved_local_path = _save_success_photo_locally(img_bytes, payload.igaCode)
            # 2) Upload to GCS
            gcs_image_path = upload_image_bytes(img_bytes, iga_code=(payload.igaCode or "Unknown"), crew_name=payload.crewName, kind="frame")
            # 3) Append event to per-crew log
            event_item = {
                "type": "liveliness_frame",
                "liveliness_status": "LIVE",
                "blink_count": result.get("blink_count", 1),
                "image_gcs_path": gcs_image_path,
                "session_id": payload.sessionId,
            }
            _, _ = append_event_to_crew_log(event_item, crew_name=payload.crewName, iga_code=payload.igaCode)
            # 4) Ticket log
            ticket_id = create_ticket({"event": "liveliness_frame", "iga_code": payload.igaCode, "crew_name": payload.crewName, **event_item})
            # 5) Return base64 so client can call /check-grooming
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            # Optional: clear session after success
            streaming_assessor.end_session(payload.sessionId)

            return {
                "event": "success",
                "liveliness_status": "LIVE",
                "blink_count": result.get("blink_count", 1),
                "captured_frame_b64": img_b64,
                "saved_photo_path": saved_local_path,
                "gcs_image_path": gcs_image_path,
                "ticket_id": ticket_id,
            }

        elif result["event"] == "progress":
            return {
                "event": "progress",
                "blink_count": result.get("blink_count", 0),
                "ear": result.get("ear", 0.0),
                "baseline_ear": result.get("baseline_ear", 0.0),
                "nose_dx": result.get("nose_dx", 0.0),
            }

        else:
            return JSONResponse(status_code=200, content=result)

    except Exception as e:
        print(f"X Error in /liveliness-frame: {str(e)}")
        return JSONResponse(status_code=500, content={"event": "error", "message": str(e)})

@app.post("/check-grooming")
async def check_grooming_endpoint(payload: GroomingRequest):
    """
    Accepts an imageBase64 (e.g., the captured success photo) and:
      - uploads the image to GCS (kind='grooming_image')
      - saves a result JSON with filename including IGA code under results/<IGA>/grooming_result_<IGA>_<HHMMSS>.json
      - appends a 'grooming_image' event to per-crew JSON
      - writes a ticket row
      - returns result text + GCS paths + today's summary
    """
    try:
        base64_image = payload.imageBase64
        if not base64_image or not isinstance(base64_image, str):
            return {"error": "Invalid image data"}

        # Strip data URL prefix if present
        clean_b64 = base64_image.split(',')[-1]
        img_bytes = base64.b64decode(clean_b64)

        # 1) Call Gemini (existing behavior)
        result_text = check_grooming(clean_b64)

        # 2) Upload the same image to GCS (grooming image)
        image_gcs_path = upload_image_bytes(
            img_bytes,
            iga_code=(payload.igaCode or "Unknown"),
            crew_name=payload.crewName,
            kind="grooming_image"
        )

        # 3) Save the result JSON with IGA in filename
        result_gcs_path = upload_grooming_result_text(
            result_text=result_text,
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
            image_gcs_path=image_gcs_path
        )

        # 4) Append event to per-crew log
        event_item = {
            "type": "grooming_image",
            "result_text": result_text,
            "image_gcs_path": image_gcs_path,
            "result_gcs_path": result_gcs_path
        }
        _, _ = append_event_to_crew_log(event_item, crew_name=payload.crewName, iga_code=payload.igaCode)

        # 5) Ticket log
        ticket_id = create_ticket({"event": "grooming_image", "iga_code": payload.igaCode, "crew_name": payload.crewName, **event_item})

        # Optional: today's latest assessments for UI
        today_summary = latest_assessments_today(payload.igaCode or "Unknown")

        return {
            "result": result_text,
            "image_gcs_path": image_gcs_path,
            "result_gcs_path": result_gcs_path,
            "ticket_id": ticket_id,
            "today_summary": today_summary
        }
    except Exception as e:
        print(f"X Error in /check-grooming: {str(e)}")
        return {"error": str(e)}

# --- (Optional) legacy routes kept as-is ---
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
        return {"error": str(e)}

@app.post("/check-grooming-video")
async def check_grooming_video_endpoint(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...)
):
    try:
        video_dir = "uploads/videos"
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, video.filename)
        with open(video_path, "wb") as buffer:
            buffer.write(await video.read())
        result = check_grooming_from_video(video_path, name, iga_code)
        return JSONResponse(content={"result": result})
    except Exception as e:
        print(f"X Error in /check-grooming-video: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("liveliness_api:app", host="0.0.0.0", port=PORT, reload=True)
