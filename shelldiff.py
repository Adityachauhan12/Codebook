# shelfdiff.py
import os
import cv2
from datetime import datetime
from typing import List, Dict, Any

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from bookfind import aggregate_titles, extract_books_from_bgr

load_dotenv()

app = FastAPI()

# Sampling config
SAMPLE_STRIDE = int(os.getenv("SAMPLE_STRIDE", "4"))           # sample every N frames
MAX_FRAME_WIDTH = int(os.getenv("MAX_FRAME_WIDTH", "1280"))    # downscale frames
MIN_VIDEO_FPS = float(os.getenv("MIN_VIDEO_FPS", "20"))        # guard for low fps metadata

# How we pick before/after
HEAD_FRACTION = float(os.getenv("HEAD_FRACTION", "0.33"))      # first third as BEFORE search window
TAIL_FRACTION = float(os.getenv("TAIL_FRACTION", "0.33"))      # last third as AFTER search window
MAX_FRAMES_PER_WINDOW = int(os.getenv("MAX_FRAMES_PER_WINDOW", "100"))

def _iter_frames(cap: cv2.VideoCapture, start: int, end: int, stride: int, limit: int) -> List:
    frames = []
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, start))
    fnum = start
    while fnum <= end and len(frames) < limit:
        ok, frame = cap.read()
        if not ok:
            break
        if (fnum - start) % stride == 0:
            h, w = frame.shape[:2]
            if w > MAX_FRAME_WIDTH:
                s = MAX_FRAME_WIDTH / float(w)
                frame = cv2.resize(frame, (int(w*s), int(h*s)))
            frames.append(frame)
        fnum += 1
    return frames

def _best_frame_by_count(frames: List) -> Any:
    # Pick the frame with the most distinct titles to represent the window
    best = None
    best_count = -1
    for f in frames:
        titles = extract_books_from_bgr(f)
        if len(titles) > best_count:
            best = f
            best_count = len(titles)
    return best

@app.post("/shelf-diff")
async def shelf_diff(file: UploadFile = File(...)):
    try:
        data = await file.read()
        # Write to temp file for OpenCV
        import tempfile, os
        suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            cap = cv2.VideoCapture(tmp.name)
            if not cap.isOpened():
                return JSONResponse({"error": "cannot_open_video"}, status_code=400)

            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if fps < 1.0:
                fps = 25.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total <= 0:
                # fallback counting
                while True:
                    ok, _ = cap.read()
                    if not ok:
                        break
                    total += 1
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

            # Define windows
            head_end = max(0, int(total * HEAD_FRACTION) - 1)
            tail_start = max(0, int(total * (1.0 - TAIL_FRACTION)))

            head_frames = _iter_frames(cap, 0, head_end, SAMPLE_STRIDE, MAX_FRAMES_PER_WINDOW)
            tail_frames = _iter_frames(cap, tail_start, total - 1, SAMPLE_STRIDE, MAX_FRAMES_PER_WINDOW)
            cap.release()

        # Choose representative frames by max title count, and also aggregate across windows
        before_frame = _best_frame_by_count(head_frames) if head_frames else None
        after_frame  = _best_frame_by_count(tail_frames) if tail_frames else None

        before_titles = aggregate_titles(head_frames) if head_frames else set()
        after_titles  = aggregate_titles(tail_frames) if tail_frames else set()

        # If representative frames are found, give them extra weight (union)
        if before_frame is not None:
            before_titles |= extract_books_from_bgr(before_frame)
        if after_frame is not None:
            after_titles  |= extract_books_from_bgr(after_frame)

        taken    = sorted(list(before_titles - after_titles))
        returned = sorted(list(after_titles - before_titles))

        result: Dict[str, Any] = {
            "timestamp_utc": datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
            "books_taken": taken,
            "books_returned": returned,
            "before_titles": sorted(list(before_titles)),
            "after_titles": sorted(list(after_titles)),
            "debug": {
                "total_frames": total,
                "fps": fps,
                "head_frames": len(head_frames),
                "tail_frames": len(tail_frames),
                "conf": os.getenv("BOOK_CONF_THRESHOLD", "0.2"),
                "iou": os.getenv("BOOK_IOU_THRESHOLD", "0.6"),
                "class_filter": os.getenv("BOOK_CLASS_NAMES", ""),
            }
        }
        return JSONResponse(result, status_code=200)
    except Exception as e:
        return JSONResponse({"books_taken": [], "books_returned": [], "error": str(e)}, status_code=200)
