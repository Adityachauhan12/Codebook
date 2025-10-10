# shelfdiff.py
import os
import io
import cv2
import json
import math
import tempfile
from datetime import datetime
from typing import List, Dict, Any

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from bookfind import aggregate_titles

load_dotenv()

app = FastAPI()

EARLY_SECONDS = float(os.getenv("EARLY_SECONDS", "5.0"))
LATE_SECONDS  = float(os.getenv("LATE_SECONDS", "5.0"))
SAMPLE_STRIDE = int(os.getenv("SAMPLE_STRIDE", "5"))
MAX_FRAMES_PER_WINDOW = int(os.getenv("MAX_FRAMES_PER_WINDOW", "60"))
MAX_FRAME_WIDTH = int(os.getenv("MAX_FRAME_WIDTH", "1280"))  # downscale frames to speed up

def _sample_window_frames(cap: cv2.VideoCapture, start_f: int, end_f: int, stride: int, limit: int) -> List[Any]:
    frames = []
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, start_f))
    fnum = start_f
    while fnum <= end_f and len(frames) < limit:
        ok, frame = cap.read()
        if not ok:
            break
        if (fnum - start_f) % stride == 0:
            h, w = frame.shape[:2]
            if w > MAX_FRAME_WIDTH:
                s = MAX_FRAME_WIDTH / float(w)
                frame = cv2.resize(frame, (int(w*s), int(h*s)))
            frames.append(frame)
        fnum += 1
    return frames

@app.post("/shelf-diff")
async def shelf_diff(file: UploadFile = File(...)):
    try:
        data = await file.read()
        suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            cap = cv2.VideoCapture(tmp.name)
            if not cap.isOpened():
                return JSONResponse({"error": "cannot_open_video"}, status_code=400)

            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total <= 0:
                # fallback count
                while True:
                    ok, _ = cap.read()
                    if not ok:
                        break
                    total += 1
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

            early_end  = min(total - 1, int(EARLY_SECONDS * fps))
            late_start = max(0, total - int(LATE_SECONDS * fps))

            early_frames = _sample_window_frames(cap, 0, early_end, SAMPLE_STRIDE, MAX_FRAMES_PER_WINDOW)
            late_frames  = _sample_window_frames(cap, late_start, total - 1, SAMPLE_STRIDE, MAX_FRAMES_PER_WINDOW)
            cap.release()

        early_titles = aggregate_titles(early_frames)
        late_titles  = aggregate_titles(late_frames)

        taken    = sorted(list(early_titles - late_titles))
        returned = sorted(list(late_titles - early_titles))

        result: Dict[str, Any] = {
            "timestamp_utc": datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
            "books_taken": taken,
            "books_returned": returned,
            "early_titles": sorted(list(early_titles)),
            "late_titles": sorted(list(late_titles)),
        }
        return JSONResponse(result, status_code=200)
    except Exception as e:
        # Return 200 with error info so client still sees partial context
        return JSONResponse({"books_taken": [], "books_returned": [], "error": str(e)}, status_code=200)
