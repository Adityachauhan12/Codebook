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

EARLY_SECONDS = float(os.getenv("EARLY_SECONDS", "5.0"))   # first N seconds
LATE_SECONDS = float(os.getenv("LATE_SECONDS", "5.0"))     # last N seconds
SAMPLE_STRIDE = int(os.getenv("SAMPLE_STRIDE", "5"))       # sample every N frames in each window
MAX_FRAMES_PER_WINDOW = int(os.getenv("MAX_FRAMES_PER_WINDOW", "60"))

def _sample_window_frames(cap: cv2.VideoCapture, start_f: int, end_f: int, stride: int, limit: int) -> List[Any]:
    frames = []
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, start_f))
    fnum = start_f
    while fnum <= end_f and len(frames) < limit:
        ok, frame = cap.read()
        if not ok:
            break
        # pick only on stride
        if (fnum - start_f) % stride == 0:
            # Optional resize to speed up
            h, w = frame.shape[:2]
            if w > 1280:
                s = 1280.0 / w
                frame = cv2.resize(frame, (int(w*s), int(h*s)))
            frames.append(frame)
        fnum += 1
    return frames

@app.post("/shelf-diff")
async def shelf_diff(file: UploadFile = File(...)):
    try:
        # Write uploaded video to a temp file for OpenCV
        data = await file.read()
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename or '')[1] or ".mp4", delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            cap = cv2.VideoCapture(tmp.name)
            if not cap.isOpened():
                return JSONResponse({"error": "cannot_open_video"}, status_code=400)

            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total <= 0:
                # fallback: iterate until end to count frames
                total = 0
                while True:
                    ok, _ = cap.read()
                    if not ok:
                        break
                    total += 1
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

            early_end = min(total - 1, int(EARLY_SECONDS * fps))
            late_start = max(0, total - int(LATE_SECONDS * fps))

            early_frames = _sample_window_frames(cap, 0, early_end, SAMPLE_STRIDE, MAX_FRAMES_PER_WINDOW)
            late_frames = _sample_window_frames(cap, late_start, total - 1, SAMPLE_STRIDE, MAX_FRAMES_PER_WINDOW)

            cap.release()

        early_titles = aggregate_titles(early_frames)
        late_titles = aggregate_titles(late_frames)

        taken = sorted(list(early_titles - late_titles))
        returned = sorted(list(late_titles - early_titles))

        result: Dict[str, Any] = {
            "timestamp_utc": datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
            "books_taken": taken,
            "books_returned": returned,
            "early_titles": sorted(list(early_titles)),
            "late_titles": sorted(list(late_titles)),
            "fps_assumed": fps,
        }
        return JSONResponse(result, status_code=200)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
