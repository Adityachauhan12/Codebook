# app/capture_photo.py
import cv2
import os
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, List
from ultralytics import YOLO
from app.face_identify import identify_face
from PIL import Image
import numpy as np

# Tunables
DEFAULT_WAIT_SECONDS = 3.0
YOLO_CONFIDENCE_THRESHOLD = 0.6
PERSON_CLASS_ID = 0  # verify with your model
FACE_MAX_SAMPLES = 20             # collect more face crops while person is present
FACE_MIN_SIZE = (80, 80)          # discard tiny faces
AFTER_MAX_SEARCH_SECONDS = 8.0    # how long to look for a changed AFTER frame
MIN_CROP_W, MIN_CROP_H = 220, 220 # enforce >= 220x220 for OCR (>= 50x50 per DI doc)  # see doc: min 50x50 [1](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/model-overview?view=doc-intel-4.0.0)
STABILITY_BUFFER = 6              # keep last N clean shelf frames
MOTION_DIFF_THRESHOLD = 7.5       # minimal mean abs diff to consider motion starting in shelf ROI
HIST_BINS = 32                    # histogram bins per channel
FACE_MARGIN = 0.18                # extra margin around face crop

MODEL_PATH = "faced_model/model.pt"
CAPTURED_DIR = Path("captured_frames")
FACES_DIR = Path("detected_faces")
CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
FACES_DIR.mkdir(parents=True, exist_ok=True)

yolo_model = YOLO(MODEL_PATH)
haar_face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

def _detect_person(frame) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    results = yolo_model(frame)[0]
    best = None
    best_conf = 0.0
    for box in results.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        if cls_id == PERSON_CLASS_ID and conf > YOLO_CONFIDENCE_THRESHOLD and conf > best_conf:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            best = (x1, y1, x2, y2)
            best_conf = conf
    return (best is not None), best

def _detect_face_local(frame_gray) -> List[Tuple[int, int, int, int]]:
    faces = haar_face.detectMultiScale(frame_gray, scaleFactor=1.1, minNeighbors=5, minSize=FACE_MIN_SIZE)
    return list(faces)

def _crop_roi(frame, roi: Optional[Dict[str, int]]):
    if roi and all(k in roi for k in ("x", "y", "w", "h")) and roi["w"] > 0 and roi["h"] > 0:
        x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
        h_frame, w_frame = frame.shape[:2]
        x = max(0, min(x, w_frame - 1))
        y = max(0, min(y, h_frame - 1))
        w = max(1, min(w, w_frame - x))
        h = max(1, min(h, h_frame - y))
        return frame[y:y+h, x:x+w]
    return frame

def _ensure_min_dimensions(img, min_w: int = MIN_CROP_W, min_h: int = MIN_CROP_H):
    h, w = img.shape[:2]
    if w < min_w or h < min_h:
        img = cv2.resize(img, (max(w, min_w), max(h, min_h)), interpolation=cv2.INTER_CUBIC)
    return img

def _write_img(path: str, img):
    img = _ensure_min_dimensions(img)
    cv2.imwrite(path, img)

def _ahash(img_path: str, size: int = 8) -> int:
    img = Image.open(img_path).convert("L").resize((size, size), Image.BILINEAR)
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, p in enumerate(pixels):
        bits |= (1 if p >= avg else 0) << i
    return bits

def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")

def _mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(a, b)))

def _hsv_hist(img: np.ndarray, bins: int = HIST_BINS) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = []
    for ch in range(3):
        h = cv2.calcHist([hsv], [ch], None, [bins], [0, 256])
        h = cv2.normalize(h, h).flatten()
        hist.append(h)
    return np.concatenate(hist)

def _hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    # Bhattacharyya distance-like: 1 - correlation
    return float(1.0 - cv2.compareHist(a.astype('float32'), b.astype('float32'), cv2.HISTCMP_CORREL))

def _laplacian_sharpness(img) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

def _brightness_score(img) -> float:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    return float(np.mean(v))

def _add_margin_box(x, y, w, h, frame_shape, margin_ratio=FACE_MARGIN):
    H, W = frame_shape[:2]
    mx = int(w * margin_ratio)
    my = int(h * margin_ratio)
    x2 = max(0, x - mx)
    y2 = max(0, y - my)
    w2 = min(W - x2, w + 2 * mx)
    h2 = min(H - y2, h + 2 * my)
    return x2, y2, w2, h2

def analyze_video(
    video_path: str,
    shelf_roi: Optional[Dict[str, int]] = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    min_change_hamming: int = 8,
    min_hist_diff: float = 0.18,
) -> Tuple[Optional[str], Optional[str], str, str]:
    """
    Single-pass analysis:
      BEFORE = last *stable* shelf frame (no person, low motion) immediately before interaction starts.
      AFTER  = first shelf frame after person leaves *and* differs from BEFORE by both hash & histogram thresholds.
      Face   = best of multiple crops while person is present (largest + sharpest + bright), with margin.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    before_path = str(CAPTURED_DIR / "before.jpg")
    after_path  = str(CAPTURED_DIR / "after.jpg")
    face_path   = str(FACES_DIR / f"face_{os.getpid()}.jpg")

    # Buffers
    last_clean_buffer: List[np.ndarray] = []  # store last few clean shelf frames for stability selection
    person_present = False
    last_person_time = None

    # face candidates
    face_samples: List[Tuple[float, float, float, np.ndarray]] = []  # (area, sharp, bright, crop)

    prev_shelf: Optional[np.ndarray] = None
    before_hist: Optional[np.ndarray] = None
    hb: Optional[int] = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        shelf_frame = _crop_roi(frame, shelf_roi)
        person_flag, person_box = _detect_person(frame)

        # strengthen person presence using local face detection
        if person_flag:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = _detect_face_local(gray)
            person_flag = person_flag and (len(faces) > 0)

        # compute motion in shelf ROI
        motion = 0.0
        if prev_shelf is not None:
            motion = _mean_abs_diff(shelf_frame, prev_shelf)
        prev_shelf = shelf_frame.copy()

        if not person_flag:
            # collect clean shelf frames while there's little motion
            if motion < MOTION_DIFF_THRESHOLD:
                last_clean_buffer.append(shelf_frame.copy())
                if len(last_clean_buffer) > STABILITY_BUFFER:
                    last_clean_buffer.pop(0)

        if person_flag and not person_present:
            # person just appeared -> choose the most stable clean shelf as BEFORE
            if last_clean_buffer:
                # stability heuristic: pick frame with minimal motion to its neighbors
                # here simply take the middle buffer element (most stable region)
                mid_idx = len(last_clean_buffer) // 2
                before = last_clean_buffer[mid_idx]
            else:
                before = shelf_frame
            _write_img(before_path, before)
            before_hist = _hsv_hist(before)
            hb = _ahash(before_path)
            person_present = True
            last_person_time = time.monotonic()

        if person_flag and person_present:
            # collect face samples
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = _detect_face_local(gray)
            for (x, y, w, h) in faces:
                x2, y2, w2, h2 = _add_margin_box(x, y, w, h, frame.shape, FACE_MARGIN)
                crop = frame[y2:y2+h2, x2:x2+w2]
                if crop.size == 0:
                    continue
                area = w2 * h2
                sharp = _laplacian_sharpness(crop)
                bright = _brightness_score(crop)
                face_samples.append((area, sharp, bright, crop))
                if len(face_samples) > FACE_MAX_SAMPLES:
                    face_samples.pop(0)
            last_person_time = time.monotonic()

        if not person_flag and person_present:
            # person gone; wait stabilization then enforce change vs BEFORE
            now = time.monotonic()
            if last_person_time is None:
                last_person_time = now
            if now - last_person_time >= wait_seconds:
                start = time.monotonic()
                while True:
                    after_candidate = shelf_frame.copy()
                    _write_img(after_path, after_candidate)
                    if hb is None or before_hist is None:
                        break
                    ha = _ahash(after_path)
                    after_hist = _hsv_hist(after_candidate)
                    changed = (_hamming(hb, ha) >= min_change_hamming) or (_hist_distance(before_hist, after_hist) >= min_hist_diff)
                    if changed:
                        break
                    if time.monotonic() - start > AFTER_MAX_SEARCH_SECONDS:
                        break
                    # read next frame and try again
                    ret2, frame2 = cap.read()
                    if not ret2:
                        break
                    shelf_frame = _crop_roi(frame2, shelf_roi)
                break

    cap.release()

    # choose best face sample (largest + sharpest + bright)
    person_id, person_name = None, None
    if face_samples:
        face_samples.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        best_crop = face_samples[0][3]
        _write_img(face_path, best_crop)
        person_id, person_name = identify_face(face_path)

