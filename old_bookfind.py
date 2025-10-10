# bookfind.py
import os
import io
from typing import List, Set, Dict, Any, Tuple
import cv2
from ultralytics import YOLO
from dotenv import load_dotenv
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from rapidfuzz import process, fuzz, utils
import numpy as np
import uuid

load_dotenv()

# Azure OCR (Document Intelligence Read)
AZURE_OCR_ENDPOINT = os.getenv("AZURE_OCR_ENDPOINT")
AZURE_OCR_KEY = os.getenv("AZURE_OCR_KEY")
doc_client = DocumentIntelligenceClient(
    endpoint=AZURE_OCR_ENDPOINT,
    credential=AzureKeyCredential(AZURE_OCR_KEY)
)

# YOLO config
YOLO_BOOK_WEIGHTS = os.getenv("YOLO_BOOK_WEIGHTS", "model.pt")
BOOK_CONF_THRESHOLD = float(os.getenv("BOOK_CONF_THRESHOLD", "0.2"))
BOOK_IOU_THRESHOLD = float(os.getenv("BOOK_IOU_THRESHOLD", "0.6"))
BOOK_CLASS_NAMES = [s.strip().lower() for s in os.getenv("BOOK_CLASS_NAMES", "").split(",") if s.strip()]

# Debug
DEBUG_SAVE = os.getenv("DEBUG_SAVE", "1") == "1"
DEBUG_DIR = os.getenv("DEBUG_DIR", "captures/debug")
os.makedirs(DEBUG_DIR, exist_ok=True)

book_model = YOLO(YOLO_BOOK_WEIGHTS)

# Known titles to normalize OCR (extend with real library)
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

FUZZY_MIN_SCORE = int(os.getenv("FUZZY_MIN_SCORE", "78"))
CROP_PAD = int(os.getenv("CROP_PAD", "18"))
OCR_MIN_SIDE = int(os.getenv("OCR_MIN_SIDE", "50"))
OCR_MAX_SIDE = int(os.getenv("OCR_MAX_SIDE", "10000"))
MIN_TEXT_LEN = int(os.getenv("MIN_TEXT_LEN", "5"))

def _resize_for_ocr(bgr):
    h, w = bgr.shape[:2]
    # ensure min side >= 50, max side <= 10000 (DI Read constraints)
    scale_up = max(1.0, OCR_MIN_SIDE / max(1, min(h, w)))
    scale = scale_up
    if max(h*scale, w*scale) > OCR_MAX_SIDE:
        scale = OCR_MAX_SIDE / max(h, w)
    if abs(scale - 1.0) > 1e-3:
        nh, nw = max(1, int(h*scale)), max(1, int(w*scale))
        interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        bgr = cv2.resize(bgr, (nw, nh), interpolation=interp)
    return bgr

def _ocr_read(bgr) -> str:
    # Send as PNG to Read
    ok, buf = cv2.imencode(".png", bgr)
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

def _ocr_roi_multi(bgr) -> str:
    # Try 0, 90, 270 degrees for vertical spines
    texts = []
    for k, rot in enumerate([0, 1, 3]):  # cv2 rotateCode: 0=none, 1=90CW, 3=90CCW via rotate(...)
        img = bgr.copy()
        if rot == 1:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif rot == 3:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        try:
            txt = _ocr_read(_resize_for_ocr(img))
            texts.append(txt)
        except Exception:
            texts.append("")
    # choose the longest non-empty text
    texts = sorted(texts, key=lambda t: len(t or ""), reverse=True)
    return texts[0] if texts else ""

def _normalize_title(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or len(raw) < MIN_TEXT_LEN:
        return ""
    match = process.extractOne(raw, KNOWN_BOOKS, scorer=fuzz.WRatio, processor=utils.default_process)
    if match and match[1] >= FUZZY_MIN_SCORE:
        return match[0]
    return utils.default_process(raw) or raw

def _class_name(results, cls_id: int) -> str:
    # Map class id to name if available
    names = getattr(results, "names", None)
    if isinstance(names, dict):
        return str(names.get(cls_id, "")).lower()
    return ""

def _save_debug(img, prefix):
    if not DEBUG_SAVE:
        return
    out = os.path.join(DEBUG_DIR, f"{prefix}_{uuid.uuid4().hex[:8]}.jpg")
    cv2.imwrite(out, img)

def _detect_books(img_bgr):
    # Run YOLO with custom thresholds
    res = book_model(img_bgr, conf=BOOK_CONF_THRESHOLD, iou=BOOK_IOU_THRESHOLD, verbose=False)[0]
    dets = []
    for b in res.boxes:
        conf = float(b.conf[0])
        cls_id = int(b.cls[0]) if b.cls is not None else -1
        name = _class_name(res, cls_id)
        if BOOK_CLASS_NAMES:
            if name not in BOOK_CLASS_NAMES:
                continue
        dets.append((b, name, conf))
    return res, dets

def extract_books_from_bgr(img) -> Set[str]:
    if img is None:
        return set()
    h, w = img.shape[:2]
    titles: Set[str] = set()
    res, dets = _detect_books(img)

    # If no detections, try slight downscale for speed and re-run
    if not dets and max(h, w) > 1280:
        s = 1280.0 / max(h, w)
        img_small = cv2.resize(img, (int(w*s), int(h*s)))
        res, dets = _detect_books(img_small)
        # remap boxes to original scale
        if dets:
            scale = 1.0 / s
            for b, name, conf in dets:
                b.xyxy *= scale

    for b, name, conf in dets:
        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
        x1 = max(0, x1 - CROP_PAD); y1 = max(0, y1 - CROP_PAD)
        x2 = min(w, x2 + CROP_PAD); y2 = min(h, y2 + CROP_PAD)
        roi = img[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        _save_debug(roi, "roi")
        try:
            raw = _ocr_roi_multi(roi)
        except Exception:
            raw = ""
        norm = _normalize_title(raw)
        if norm:
            titles.add(norm)

    # Fallback: full-frame OCR if no titles found
    if not titles:
        try:
            # center crop where shelf likely is (middle band)
            band_top = int(h * 0.2)
            band_bot = int(h * 0.8)
            band = img[band_top:band_bot, :]
            _save_debug(band, "band")
            raw = _ocr_roi_multi(band)
            norm = _normalize_title(raw)
            if norm:
                # Split by common separators to harvest multiple titles
                for piece in [p.strip() for p in raw.replace("|", "\n").replace("/", "\n").split("\n")]:
                    n2 = _normalize_title(piece)
                    if n2:
                        titles.add(n2)
        except Exception:
            pass

    return titles

def extract_books(image_path: str) -> Set[str]:
    img = cv2.imread(image_path)
    return extract_books_from_bgr(img)

def aggregate_titles(frames: List[Any]) -> Set[str]:
    agg: Set[str] = set()
    for f in frames:
        agg |= extract_books_from_bgr(f)
    return agg
