# bookfind.py
import os
import io
from typing import List, Set, Dict, Any
import cv2
from ultralytics import YOLO
from dotenv import load_dotenv
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from rapidfuzz import process, fuzz, utils

load_dotenv()

AZURE_OCR_ENDPOINT = os.getenv("AZURE_OCR_ENDPOINT")
AZURE_OCR_KEY = os.getenv("AZURE_OCR_KEY")
doc_client = DocumentIntelligenceClient(endpoint=AZURE_OCR_ENDPOINT, credential=AzureKeyCredential(AZURE_OCR_KEY))

YOLO_BOOK_WEIGHTS = os.getenv("YOLO_BOOK_WEIGHTS", "model.pt")
BOOK_CONF_THRESHOLD = float(os.getenv("BOOK_CONF_THRESHOLD", "0.35"))
book_model = YOLO(YOLO_BOOK_WEIGHTS)

KNOWN_BOOKS = [
    "Scrum — Jeff Sutherland & JJ Sutherland",
    "The Innovator’s Dilemma by Clayton M. Christensen",
    "Human + Machine by Paul R. Daugherty and H. James Wilson",
    "How They Started Digital by David Lester",
    "Physics of the Future by Michio Kaku",
    "Contagious by Jonah Berger",
    "Never Split the Difference by Chris Voss",
    "Antifragile by Nassim Nicholas Taleb",
    "Hit Refresh by Satya Nadella",
    "Essentialism by Greg McKeown",
    "Just Listen by Mark Goulston",
    "Open Shift by Arnaud Pascoe",
    "The Singularity Is Nearer — Ray Kurzweil",
    "Mastering the Data Paradox — Nitin Seth",
    "The Stoic Mindset — Mark Tuitert",
    "Lilliput Land — Rama Bijapurkar",
    "The Coming Wave — Mustafa Suleyman with Michael Bhaskar",
    "AI, Analytics, and the Future",
]

FUZZY_MIN_SCORE = int(os.getenv("FUZZY_MIN_SCORE", "80"))
OCR_MIN_SIDE = int(os.getenv("OCR_MIN_SIDE", "50"))        # per DI: >=50 pixels
OCR_MAX_SIDE = int(os.getenv("OCR_MAX_SIDE", "10000"))     # per DI: <=10000 pixels
CROP_PAD = int(os.getenv("CROP_PAD", "16"))                # widen crop for OCR

def _resize_for_ocr(bgr):
    h, w = bgr.shape[:2]
    # Compute scale to satisfy both min and max constraints
    scale_up = max(1.0, OCR_MIN_SIDE / max(1, min(h, w)))
    scale_down = min(1.0, OCR_MAX_SIDE / max(h, w))
    scale = scale_up
    if max(h*scale, w*scale) > OCR_MAX_SIDE:
        scale = scale_down
    if abs(scale - 1.0) > 1e-3:
        nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
        interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
        bgr = cv2.resize(bgr, (nw, nh), interpolation=interp)
    return bgr

def _ocr_bytes(img_bgr_roi) -> str:
    try:
        img_bgr_roi = _resize_for_ocr(img_bgr_roi)
        ok, buf = cv2.imencode(".png", img_bgr_roi)
        if not ok:
            return ""
        stream = io.BytesIO(buf.tobytes())
        poller = doc_client.begin_analyze_document(model_id="prebuilt-read", body=stream)
        result = poller.result()
        lines = []
        for page in result.pages or []:
            for line in page.lines or []:
                lines.append(line.content)
        return " ".join(lines)
    except Exception:
        # Swallow per-ROI OCR errors (e.g., occasional invalid crops)
        return ""

def _normalize_title(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    match = process.extractOne(raw, KNOWN_BOOKS, scorer=fuzz.WRatio, processor=utils.default_process)
    if match and match[1] >= FUZZY_MIN_SCORE:
        return match[0]
    return utils.default_process(raw) or raw

def extract_books_from_bgr(img) -> Set[str]:
    if img is None:
        return set()
    h, w = img.shape[:2]
    results = book_model(img, verbose=False)[0]
    titles: Set[str] = set()
    for b in results.boxes:
        conf = float(b.conf[0])
        if conf < BOOK_CONF_THRESHOLD:
            continue
        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
        x1 = max(0, x1 - CROP_PAD); y1 = max(0, y1 - CROP_PAD)
        x2 = min(w, x2 + CROP_PAD); y2 = min(h, y2 + CROP_PAD)
        roi = img[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        raw = _ocr_bytes(roi)
        norm = _normalize_title(raw)
        if norm:
            titles.add(norm)
    return titles

def extract_books(image_path: str) -> Set[str]:
    img = cv2.imread(image_path)
    return extract_books_from_bgr(img)

def aggregate_titles(frames: List[Any]) -> Set[str]:
    agg: Set[str] = set()
    for f in frames:
        agg |= extract_books_from_bgr(f)
    return agg

def compare_shelf(before_image: str, after_image: str) -> Dict[str, Any]:
    before = extract_books(before_image)
    after = extract_books(after_image)
    taken = sorted(list(before - after))
    returned = sorted(list(after - before))
    return {"books_taken": taken, "books_returned": returned, "before_count": len(before), "after_count": len(after)}
