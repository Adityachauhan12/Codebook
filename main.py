from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from pathlib import Path
import uuid
from typing import List, Optional, Dict
from app.capture_photo import analyze_video
from app.book_find import extract_text_from_image, get_books_present_via_gpt, ALL_BOOKS, fuzzy_match_books
from app.compare_books import compare_book_lists
import cv2
import os

CAPTURED_DIR = Path("captured_frames")
DETECTED_DIR = Path("detected_faces")

app = FastAPI()

@app.on_event("startup")
def startup():
    CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
    DETECTED_DIR.mkdir(parents=True, exist_ok=True)
    print("📚 Books-Cam System started")

@app.get("/")
def root():
    return {"message": "Books-Cam System is running", "status": "ok"}

def parse_books_param(books_list: Optional[List[str]], books_csv: Optional[str]) -> List[str]:
    if books_list and isinstance(books_list, list):
        cleaned = [b.strip() for b in books_list if isinstance(b, str) and b.strip()]
        if cleaned:
            return cleaned
    if books_csv and isinstance(books_csv, str):
        parts = [p.strip() for p in books_csv.split(",") if p.strip()]
        if parts:
            return parts
    return ALL_BOOKS

def _crop_and_save(img_path: str, roi: Dict[str,int], out_path: str) -> str:
    img = cv2.imread(img_path)
    if img is None:
        raise RuntimeError(f"Cannot read image: {img_path}")
    H, W = img.shape[:2]
    x = max(0, min(roi['x'], W-1)); y = max(0, min(roi['y'], H-1))
    w = max(1, min(roi['w'], W-x)); h = max(1, min(roi['h'], H-y))
    crop = img[y:y+h, x:x+w]
    cv2.imwrite(out_path, crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return out_path

@app.post("/monitor-bookshelf")
async def monitor_bookshelf(
    file: UploadFile = File(...),
    shelf_x: Optional[int] = Form(default=None),
    shelf_y: Optional[int] = Form(default=None),
    shelf_w: Optional[int] = Form(default=None),
    shelf_h: Optional[int] = Form(default=None),
    wait_seconds: float = Form(default=3.0),
    min_change_hamming: int = Form(default=8),
    min_hist_diff: float = Form(default=0.18),
    books: Optional[List[str]] = Form(default=None),
    books_csv: Optional[str] = Form(default=None),
    use_gpt: bool = Form(default=False),
):
    print(f"\n{'='*60}")
    print(f"📹 New request: {file.filename}")
    print(f"{'='*60}")
    
    ext = Path(file.filename).suffix.lower()
    if ext not in {".mp4", ".mov", ".avi", ".mkv"}:
        raise HTTPException(status_code=400, detail="Unsupported video format")
    video_path = CAPTURED_DIR / f"{uuid.uuid4()}{ext}"
    try:
        with video_path.open("wb") as f:
            while True:
                chunk = await file.read(1024*1024)
                if not chunk:
                    break
                f.write(chunk)
        print(f"✅ Video saved: {video_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save video: {e}")

    shelf_roi = None
    if all(v is not None for v in (shelf_x, shelf_y, shelf_w, shelf_h)) and (shelf_w or 0) > 0 and (shelf_h or 0) > 0:
        shelf_roi = {"x": shelf_x, "y": shelf_y, "w": shelf_w, "h": shelf_h}
        print(f"📐 Using shelf ROI: {shelf_roi}")
    else:
        print(f"📐 No shelf ROI specified, using full frame")

    known_titles = parse_books_param(books, books_csv)
    print(f"📚 Monitoring {len(known_titles)} books")

    try:
        print(f"\n--- Video Analysis ---")
        person_id, person_name, before_img, after_img, changed_rois = analyze_video(
            str(video_path),
            shelf_roi=shelf_roi,
            wait_seconds=wait_seconds,
            min_change_hamming=min_change_hamming,
            min_hist_diff=min_hist_diff,
        )

        print(f"\n--- OCR Processing (Changed Cells) ---")
        before_books_all: List[str] = []
        after_books_all: List[str] = []
        debug_cells = []
        if not changed_rois:
            changed_rois = [{"x":0,"y":0,"w":0,"h":0,"cell":"all","score":0.0}]
        for idx, roi in enumerate(changed_rois):
            b_out = str(CAPTURED_DIR / f"roi_before_{idx}.jpg")
            a_out = str(CAPTURED_DIR / f"roi_after_{idx}.jpg")
            roi_use = {"x":0,"y":0,"w":999999,"h":999999} if roi["w"] == 0 or roi["h"] == 0 else roi
            _crop_and_save(before_img, roi_use, b_out)
            _crop_and_save(after_img, roi_use, a_out)

            b_lines = extract_text_from_image(b_out)
            a_lines = extract_text_from_image(a_out)

            if use_gpt:
                b_gpt = get_books_present_via_gpt(b_lines, known_titles)
                a_gpt = get_books_present_via_gpt(a_lines, known_titles)
            else:
                b_gpt = []; a_gpt = []
            b_books = b_gpt if b_gpt else fuzzy_match_books(b_lines, known_titles)
            a_books = a_gpt if a_gpt else fuzzy_match_books(a_lines, known_titles)

            before_books_all.extend(b_books)
            after_books_all.extend(a_books)
            debug_cells.append({
                "cell": roi.get("cell", str(idx)),
                "score": roi.get("score", 0.0),
                "before_lines": len(b_lines),
                "after_lines": len(a_lines),
                "before_books": b_books,
                "after_books": a_books
            })

        before_books_all = list(dict.fromkeys(before_books_all))
        after_books_all = list(dict.fromkeys(after_books_all))
        print(f"\n--- Book Identification (Aggregated) ---")
        diff = compare_book_lists(before_books_all, after_books_all)

        result = {
            "personId": person_id or "Unknown",
            "personName": person_name or "Unknown",
            "taken": diff["removed"],
            "placed": diff["added"],
            "debug": {
                "changed_cells": debug_cells,
                "before_books": before_books_all,
                "after_books": after_books_all,
                "before_image": before_img,
                "after_image": after_img
            }
        }

        print(f"\n--- Results ---")
        print(f"Person: {result['personName']}")
        print(f"Taken: {result['taken']}")
        print(f"Placed: {result['placed']}")
        print(f"{'='*60}\n")

        return JSONResponse(result)
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if video_path.exists():
            try:
                os.remove(video_path)
            except Exception:
                pass
