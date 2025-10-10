# bookfind.py
import os
import io
from typing import List, Set, Tuple, Dict, Any
import cv2
from ultralytics import YOLO
from dotenv import load_dotenv
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from rapidfuzz import process, fuzz, utils

load_dotenv()

# Azure OCR (Document Intelligence Read)
AZURE_OCR_ENDPOINT = os.getenv("AZURE_OCR_ENDPOINT")  # e.g., https://lab37-di-doc-mining.cognitiveservices.azure.com/
AZURE_OCR_KEY = os.getenv("AZURE_OCR_KEY")
doc_client = DocumentIntelligenceClient(endpoint=AZURE_OCR_ENDPOINT, credential=AzureKeyCredential(AZURE_OCR_KEY))

# YOLO models
YOLO_BOOK_WEIGHTS = os.getenv("YOLO_BOOK_WEIGHTS", "model.pt")  # custom trained for books
BOOK_CONF_THRESHOLD = float(os.getenv("BOOK_CONF_THRESHOLD", "0.35"))
book_model = YOLO(YOLO_BOOK_WEIGHTS)

# Known titles to normalize OCR (extend this list as needed)
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

def _ocr_bytes(img_bgr_roi) -> str:
    # Encode ROI to PNG and send to Read model
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
    text = " ".join(lines)
    return text

def _normalize_title(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    match = process.extractOne(
        raw,
        KNOWN_BOOKS,
        scorer=fuzz.WRatio,
        processor=utils.default_process,
    )
    if match and match[1] >= FUZZY_MIN_SCORE:
        return match[0]
    # Fallback: return cleaned raw
    return utils.default_process(raw) or raw

def extract_books(image_path: str) -> Set[str]:
    img = cv2.imread(image_path)
    if img is None:
        return set()

    h, w = img.shape[:2]
    results = book_model(img, verbose=False)[0]
    titles: Set[str] = set()

    for b in results.boxes:
        conf = float(b.conf[0])
        cls_id = int(b.cls[0]) if b.cls is not None else -1
        # If your custom model has class "book" as id 0 or named "book", trust the model; otherwise remove class check
        if conf < BOOK_CONF_THRESHOLD:
            continue

        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
        # Expand a bit for OCR
        pad = 8
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        roi = img[y1:y2, x1:x2]
        if roi.size == 0:
            continue

        raw = _ocr_bytes(roi)
        norm = _normalize_title(raw)
        if norm:
            titles.add(norm)

    return titles

def compare_shelf(before_image: str, after_image: str) -> Dict[str, Any]:
    before = extract_books(before_image)
    after = extract_books(after_image)

    taken = sorted(list(before - after))
    returned = sorted(list(after - before))
    return {
        "books_taken": taken,
        "books_returned": returned,
        "before_count": len(before),
        "after_count": len(after),
    }
