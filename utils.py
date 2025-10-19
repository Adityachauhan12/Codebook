# src/utils.py
from difflib import SequenceMatcher
from PIL import Image, ImageEnhance

def fuzzy_match(query, candidates, topk=5):
    """
    Returns top-k matches for query from candidates based on fuzzy_score.
    Each match is a dict: {"title": candidate, "score": similarity}
    """
    scored = [{"title": c, "score": fuzzy_score(query, c)} for c in candidates]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:topk]

def iou(a, b):
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
    x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
    inter_w = max(0, x2 - x1); inter_h = max(0, y2 - y1)
    inter = inter_w * inter_h
    areaA = max(1, (a[2]-a[0])*(a[3]-a[1]))
    areaB = max(1, (b[2]-b[0])*(b[3]-b[1]))
    return inter / float(areaA + areaB - inter + 1e-9)

def fuzzy_score(a, b):
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()

def preprocess_for_ocr(pil_img, target_h=800):
    w, h = pil_img.size
    if h < target_h:
        scale = target_h / h
        pil_img = pil_img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    enhancer = ImageEnhance.Contrast(pil_img)
    pil_img = enhancer.enhance(1.4)
    return pil_img
