# app/main.py
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from pathlib import Path
import uuid
from typing import List, Optional

from app.capture_photo import analyze_video
from app.book_find import extract_text_from_image, get_books_present_via_gpt, ALL_BOOKS, fuzzy_match_books
from app.compare_books import compare_book_lists

CAPTURED_DIR = Path("captured_frames")
DETECTED_DIR = Path("detected_faces")

app = FastAPI()

@app.on_event("startup")
def startup():
    CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
    DETECTED_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/")
def root():
    return {"message": "Books-Cam System is running"}

def parse_books_param(books_list: Optional[List[str]], books_csv: Optional[str]) -> List[str]:
    """
    Accept either repeated -F books=Title or -F books_csv="Title1,Title2,...".
    Falls back to ALL_BOOKS if nothing valid is provided.
    """
    if books_list and isinstance(books_list, list):
        cleaned = [b.strip() for b in books_list if isinstance(b, str) and b.strip()]
        if cleaned:
            return cleaned
    if books_csv and isinstance(books_csv, str):
        parts = [p.strip() for p in books_csv.split(",") if p.strip()]
        if parts:
            return parts
    return ALL_BOOKS

@app.post("/monitor-bookshelf")
async def monitor_bookshelf(
    file: UploadFile = File(...),
    # Shelf ROI (optional). If w/h <= 0, ROI is ignored and full frame is used for OCR.
    shelf_x: Optional[int] = Form(default=None),
    shelf_y: Optional[int] = Form(default=None),
    shelf_w: Optional[int] = Form(default=None),
    shelf_h: Optional[int] = Form(default=None),
    # Tuning knobs (optional)
    wait_seconds: float = Form(default=3.0),          # wait after last person frame before capturing AFTER
    min_change_hamming: int = Form(default=8),        # minimum aHash Hamming distance between BEFORE and AFTER
    min_hist_diff: float = Form(default=0.18),        # minimum histogram distance to consider shelf changed
    # Known books (optional; either repeated "books" or CSV "books_csv")
    books: Optional[List[str]] = Form(default=None),
    books_csv: Optional[str] = Form(default=None),
):
    # 1) Save uploaded video safely
    ext = Path(file.filename).suffix.lower()
    if ext not in {".mp4", ".mov", ".avi", ".mkv"}:
        raise HTTPException(status_code=400, detail="Unsupported video format")

    video_path = CAPTURED_DIR / f"{uuid.uuid4()}{ext}"
    try:
        with video_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save video: {e}")

    # 2) Shelf ROI (ignore invalid/zero ROI)
    shelf_roi = None
    if all(v is not None for v in (shelf_x, shelf_y, shelf_w, shelf_h)) and (shelf_w or 0) > 0 and (shelf_h or 0) > 0:
        shelf_roi = {"x": shelf_x, "y": shelf_y, "w": shelf_w, "h": shelf_h}

    # 3) Known titles
    known_titles = parse_books_param(books, books_csv)

    try:
        # 4) Single-pass analysis
        person_id, person_name, before_img, after_img = analyze_video(
            str(video_path),
            shelf_roi=shelf_roi,
            wait_seconds=wait_seconds,
            min_change_hamming=min_change_hamming,
            min_hist_diff=min_hist_diff,
        )

        # 5) OCR
        before_lines = extract_text_from_image(before_img)
        after_lines  = extract_text_from_image(after_img)

        # 6) GPT + local fuzzy fallback
        before_books_gpt = get_books_present_via_gpt(before_lines, known_titles)
        after_books_gpt  = get_books_present_via_gpt(after_lines,  known_titles)

        before_books = before_books_gpt or fuzzy_match_books(before_lines, known_titles)
        after_books  = after_books_gpt  or fuzzy_match_books(after_lines,  known_titles)

        # 7) Diff -> semantics
        diff = compare_book_lists(before_books, after_books)
        result = {
            "personId": person_id or "Unknown",
            "personName": person_name or "Unknown",
            "taken": diff["removed"],  # were in BEFORE, not in AFTER
            "placed": diff["added"],   # were not in BEFORE, appear in AFTER
            "debug": {
                "before_books": before_books,
                "after_books": after_books,
                "before_image": before_img,
                "after_image": after_img
            }
        }
        return JSONResponse(result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))