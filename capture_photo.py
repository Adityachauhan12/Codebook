import cv2
import os
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, List
from ultralytics import YOLO
from app.face_identify import identify_face
from PIL import Image
import numpy as np

# Configuration
DEFAULT_WAIT_SECONDS = 3.0
YOLO_CONFIDENCE_THRESHOLD = 0.4
PERSON_CLASS_ID = 0
FACE_MAX_SAMPLES = 30
FACE_MIN_SIZE = (80, 80)
AFTER_MAX_SEARCH_SECONDS = 8.0
MIN_CROP_W, MIN_CROP_H = 240, 240
STABILITY_BUFFER = 10
MOTION_DIFF_THRESHOLD = 8.0
HIST_BINS = 32
FACE_MARGIN = 0.25
MODEL_PATH = "yolov8n.pt"

CAPTURED_DIR = Path("captured_frames")
FACES_DIR = Path("detected_faces")
CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
FACES_DIR.mkdir(parents=True, exist_ok=True)

yolo_model = YOLO(MODEL_PATH)
haar_face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

def _detect_person(frame) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    results = yolo_model(frame, verbose=False)[0]
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
    faces = haar_face.detectMultiScale(
        frame_gray,
        scaleFactor=1.05,
        minNeighbors=4,
        minSize=FACE_MIN_SIZE,
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    return list(faces)

def _crop_roi(frame, roi: Optional[Dict[str, int]]):
    if roi and all(k in roi for k in ("x", "y", "w", "h")) and roi["w"] > 0 and roi["h"] > 0:
        x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
        H, W = frame.shape[:2]
        x = max(0, min(x, W - 1)); y = max(0, min(y, H - 1))
        w = max(1, min(w, W - x)); h = max(1, min(h, H - y))
        return frame[y:y+h, x:x+w]
    return frame

def _ensure_min_dimensions(img, min_w: int = MIN_CROP_W, min_h: int = MIN_CROP_H):
    h, w = img.shape[:2]
    if w < min_w or h < min_h:
        img = cv2.resize(img, (max(w, min_w), max(h, min_h)), interpolation=cv2.INTER_CUBIC)
    return img

def _write_img(path: str, img):
    img = _ensure_min_dimensions(img)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    enhanced = cv2.merge([l, a, b])
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    cv2.imwrite(path, enhanced, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"   💾 Saved: {path}")

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
    mx = int(w * margin_ratio); my = int(h * margin_ratio)
    x2 = max(0, x - mx); y2 = max(0, y - my)
    w2 = min(W - x2, w + 2 * mx); h2 = min(H - y2, h + 2 * my)
    return x2, y2, w2, h2

def _grid_rois(width: int, height: int, nx: int = 4, ny: int = 4) -> List[Dict[str, int]]:
    rois = []
    cell_w = width // nx
    cell_h = height // ny
    for gy in range(ny):
        for gx in range(nx):
            x = gx * cell_w
            y = gy * cell_h
            w = cell_w if gx < nx - 1 else (width - x)
            h = cell_h if gy < ny - 1 else (height - y)
            rois.append({"x": x, "y": y, "w": w, "h": h, "cell": f"{gx},{gy}"})
    return rois

def _score_roi_change(before: np.ndarray, after: np.ndarray) -> float:
    mad = _mean_abs_diff(before, after)
    hb = _hsv_hist(before); ha = _hsv_hist(after)
    hdist = _hist_distance(hb, ha)
    return 0.6 * mad + 40.0 * hdist

def analyze_video(
    video_path: str,
    shelf_roi: Optional[Dict[str, int]] = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    min_change_hamming: int = 8,
    min_hist_diff: float = 0.18,
):
    """
    Returns: (person_id, person_name, before_image_path, after_image_path, changed_rois)
    changed_rois: list of {x,y,w,h,cell,score} sorted by score desc (top 3)
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    
    print(f"📹 Analyzing video: {video_path}")
    
    before_path = str(CAPTURED_DIR / "before.jpg")
    after_path = str(CAPTURED_DIR / "after.jpg")
    face_path = str(FACES_DIR / f"face_{os.getpid()}.jpg")
    
    clean_shelf_buffer: List[np.ndarray] = []
    person_present = False
    person_ever_detected = False
    last_person_time = None
    face_samples: List[Tuple[float, float, float, np.ndarray]] = []
    prev_shelf: Optional[np.ndarray] = None
    before_hist: Optional[np.ndarray] = None
    hb: Optional[int] = None
    frame_count = 0
    
    before_frame = None
    after_frame = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_count += 1
        shelf_frame = _crop_roi(frame, shelf_roi)
        person_flag, _ = _detect_person(frame)
        
        motion = 0.0
        if prev_shelf is not None:
            motion = _mean_abs_diff(shelf_frame, prev_shelf)
        prev_shelf = shelf_frame.copy()
        
        if not person_flag and not person_ever_detected:
            if motion < MOTION_DIFF_THRESHOLD:
                clean_shelf_buffer.append(shelf_frame.copy())
                if len(clean_shelf_buffer) > STABILITY_BUFFER:
                    clean_shelf_buffer.pop(0)
        
        if person_flag and not person_present:
            print(f"👤 Person detected at frame {frame_count}")
            if clean_shelf_buffer:
                mid_idx = len(clean_shelf_buffer) // 2
                before_frame = clean_shelf_buffer[mid_idx]
                print(f"   Using clean frame from buffer (index {mid_idx}/{len(clean_shelf_buffer)})")
            else:
                before_frame = shelf_frame
                print(f"   ⚠️  No clean buffer, using current frame")
            _write_img(before_path, before_frame)
            before_hist = _hsv_hist(before_frame)
            hb = _ahash(before_path)
            person_present = True
            person_ever_detected = True
            last_person_time = time.monotonic()
            print(f"📸 BEFORE frame captured (from before person entered)")
        
        if person_flag and person_present:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = _detect_face_local(gray)
            for (x, y, w, h) in faces:
                if w < 100 or h < 100:
                    continue
                x2, y2, w2, h2 = _add_margin_box(x, y, w, h, frame.shape, FACE_MARGIN)
                crop = frame[y2:y2+h2, x2:x2+w2]
                if crop.size == 0:
                    continue
                area = w2 * h2
                sharp = _laplacian_sharpness(crop)
                bright = _brightness_score(crop)
                if sharp > 50 and 50 < bright < 200:
                    face_samples.append((area, sharp, bright, crop))
                    if len(face_samples) > FACE_MAX_SAMPLES:
                        face_samples.pop(0)
            last_person_time = time.monotonic()
        
        if not person_flag and person_present:
            now = time.monotonic()
            if last_person_time is None:
                last_person_time = now
            if now - last_person_time >= wait_seconds:
                print(f"👋 Person left at frame {frame_count}")
                print(f"   Waiting for shelf to stabilize...")
                search_start = time.monotonic()
                while time.monotonic() - search_start < AFTER_MAX_SEARCH_SECONDS:
                    ret2, frame2 = cap.read()
                    if not ret2:
                        break
                    frame_count += 1
                    shelf_frame2 = _crop_roi(frame2, shelf_roi)
                    person_returned, _ = _detect_person(frame2)
                    if person_returned:
                        print(f"   ⚠️  Person returned, continuing to wait...")
                        last_person_time = time.monotonic()
                        continue
                    _write_img(after_path, shelf_frame2)
                    if hb is None or before_hist is None:
                        after_frame = shelf_frame2
                        break
                    ha = _ahash(after_path)
                    after_hist = _hsv_hist(shelf_frame2)
                    hamming_dist = _hamming(hb, ha)
                    hist_dist = _hist_distance(before_hist, after_hist)
                    if (hamming_dist >= min_change_hamming) or (hist_dist >= min_hist_diff):
                        print(f"✅ AFTER frame captured (change detected: hamming={hamming_dist}, hist={hist_dist:.3f})")
                        after_frame = shelf_frame2
                        break
                if after_frame is None:
                    after_frame = shelf_frame
                break
    
    cap.release()
    print(f"✅ Video analysis complete ({frame_count} frames)")
    
    person_id, person_name = None, None
    if face_samples:
        print(f"🔍 Analyzing {len(face_samples)} face samples")
        face_samples.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        best_crop = face_samples[0][3]
        print(f"   Face crop size: {best_crop.shape[1]}x{best_crop.shape[0]}")
        print(f"   Face quality - Area: {face_samples[0][0]}, Sharpness: {face_samples[0][1]:.1f}, Brightness: {face_samples[0][2]:.1f}")
        face_out = str(FACES_DIR / f"face_{os.getpid()}.jpg")
        _write_img(face_out, best_crop)
        print(f"💾 Best face saved: {face_out}")
        person_id, person_name = identify_face(face_out)
    else:
        print("❌ No face samples collected")
    
    if before_frame is not None:
        _write_img(before_path, before_frame)
    if after_frame is not None:
        _write_img(after_path, after_frame)
    
    # Compute top-changed ROIs (4x4 grid)
    changed_rois: List[Dict[str, int]] = []
    if before_frame is not None and after_frame is not None:
        H, W = before_frame.shape[:2]
        rois = _grid_rois(W, H, nx=4, ny=4)
        scored = []
        for r in rois:
            b = before_frame[r['y']:r['y']+r['h'], r['x']:r['x']+r['w']]
            a = after_frame[r['y']:r['y']+r['h'], r['x']:r['x']+r['w']]
            score = _score_roi_change(b, a)
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        for s, r in scored[:3]:
            rr = dict(r)
            rr['score'] = float(s)
            changed_rois.append(rr)
        print(f"🔎 Top changed cells: {[ (r['cell'], round(r['score'],2)) for r in changed_rois ]}")
    
    return person_id, person_name, before_path, after_path, changed_rois
