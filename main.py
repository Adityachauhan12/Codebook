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
    print("📚 Books-Cam System started")

@app.get("/")
def root():
    return {"message": "Books-Cam System is running", "status": "ok"}

def parse_books_param(books_list: Optional[List[str]], books_csv: Optional[str]) -> List[str]:
    """
    Accept either repeated -F books=Title or -F books_csv="Title1,Title2,...".
    Falls back to ALL_BOOKS if nothing valid is provided.
    ALWAYS use ALL_BOOKS from book_find.py to ensure consistent format.
    """
    # For now, always use the complete ALL_BOOKS list from book_find.py
    # This ensures proper author info is available for matching
    from app.book_find import ALL_BOOKS as FULL_BOOKS
    
    # If user provided custom list, try to match against full titles
    if books_list and isinstance(books_list, list):
        cleaned = [b.strip() for b in books_list if isinstance(b, str) and b.strip()]
        if cleaned:
            # Try to find full titles from ALL_BOOKS that match user input
            matched = []
            for user_book in cleaned:
                for full_book in FULL_BOOKS:
                    if user_book.lower() in full_book.lower():
                        matched.append(full_book)
                        break
            if matched:
                return matched
    
    if books_csv and isinstance(books_csv, str):
        parts = [p.strip() for p in books_csv.split(",") if p.strip()]
        if parts:
            # Try to match against full titles
            matched = []
            for user_book in parts:
                for full_book in FULL_BOOKS:
                    if user_book.lower() in full_book.lower():
                        matched.append(full_book)
                        break
            if matched:
                return matched
    
    # Default: use complete list with authors
    return FULL_BOOKS


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
):
    """
    Monitor bookshelf video to detect:
    1. Who interacted with the shelf (Azure Face API)
    2. What books were taken or placed (Azure OCR + GPT)
    
    Returns JSON with person identity and book changes.
    """
    print(f"\n{'='*60}")
    print(f"📹 New request: {file.filename}")
    print(f"{'='*60}")
    
    # Validate and save video
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
        
        print(f"✅ Video saved: {video_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save video: {e}")
    
    # Parse shelf ROI
    shelf_roi = None
    if all(v is not None for v in (shelf_x, shelf_y, shelf_w, shelf_h)) and (shelf_w or 0) > 0 and (shelf_h or 0) > 0:
        shelf_roi = {"x": shelf_x, "y": shelf_y, "w": shelf_w, "h": shelf_h}
        print(f"📐 Using shelf ROI: {shelf_roi}")
    
    # Parse known books
    known_titles = parse_books_param(books, books_csv)
    print(f"📚 Monitoring books: {known_titles}")
    
    try:
        # Analyze video for person and shelf changes
        print(f"\n--- Video Analysis ---")
        person_id, person_name, before_img, after_img = analyze_video(
            str(video_path),
            shelf_roi=shelf_roi,
            wait_seconds=wait_seconds,
            min_change_hamming=min_change_hamming,
            min_hist_diff=min_hist_diff,
        )
        
        # Extract text from before/after images
        print(f"\n--- OCR Processing ---")
        before_lines = extract_text_from_image(before_img)
        after_lines = extract_text_from_image(after_img)
        
        # Identify books using GPT + fuzzy fallback
        print(f"\n--- Book Identification ---")
        print(f"BEFORE frame:")
        before_books_gpt = get_books_present_via_gpt(before_lines, known_titles)
        before_books = before_books_gpt if before_books_gpt else fuzzy_match_books(before_lines, known_titles)
        
        print(f"\nAFTER frame:")
        after_books_gpt = get_books_present_via_gpt(after_lines, known_titles)
        after_books = after_books_gpt if after_books_gpt else fuzzy_match_books(after_lines, known_titles)
        
        # Compare to determine changes
        print(f"\n--- Book Changes ---")
        diff = compare_book_lists(before_books, after_books)
        
        result = {
            "personId": person_id or "Unknown",
            "personName": person_name or "Unknown",
            "taken": diff["removed"],
            "placed": diff["added"],
            "debug": {
                "before_books": before_books,
                "after_books": after_books,
                "before_image": before_img,
                "after_image": after_img,
                "before_ocr_lines": len(before_lines),
                "after_ocr_lines": len(after_lines)
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
        # Cleanup uploaded video
        if video_path.exists():
            video_path.unlink()

