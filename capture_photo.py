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

# Configuration
DEFAULT_WAIT_SECONDS = 3.0
YOLO_CONFIDENCE_THRESHOLD = 0.4  # Lowered for better detection
PERSON_CLASS_ID = 0
FACE_MAX_SAMPLES = 30
FACE_MIN_SIZE = (60, 60)
AFTER_MAX_SEARCH_SECONDS = 8.0
MIN_CROP_W, MIN_CROP_H = 240, 240
STABILITY_BUFFER = 10  # Increased buffer
MOTION_DIFF_THRESHOLD = 8.0
HIST_BINS = 32
FACE_MARGIN = 0.25
MODEL_PATH = "yolov8n.pt"  # Use standard YOLOv8n

CAPTURED_DIR = Path("captured_frames")
FACES_DIR = Path("detected_faces")
CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
FACES_DIR.mkdir(parents=True, exist_ok=True)

yolo_model = YOLO(MODEL_PATH)
haar_face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

def _detect_person(frame) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    """Detect person using YOLO model"""
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
    """Detect faces using Haar cascade"""
    faces = haar_face.detectMultiScale(
        frame_gray, 
        scaleFactor=1.05, 
        minNeighbors=4, 
        minSize=FACE_MIN_SIZE,
        flags=cv2.CASCADE_SCALE_IMAGE
    )
    return list(faces)

def _crop_roi(frame, roi: Optional[Dict[str, int]]):
    """Crop frame to ROI if specified"""
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
    """Ensure image meets minimum dimensions for OCR"""
    h, w = img.shape[:2]
    if w < min_w or h < min_h:
        img = cv2.resize(img, (max(w, min_w), max(h, min_h)), interpolation=cv2.INTER_CUBIC)
    return img

def _write_img(path: str, img):
    """Write image with light enhancement for OCR"""
    img = _ensure_min_dimensions(img)
    
    # Light contrast enhancement only
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    enhanced = cv2.merge([l, a, b])
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    
    cv2.imwrite(path, enhanced, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"   💾 Saved: {path}")

def _ahash(img_path: str, size: int = 8) -> int:
    """Calculate average hash for image comparison"""
    img = Image.open(img_path).convert("L").resize((size, size), Image.BILINEAR)
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, p in enumerate(pixels):
        bits |= (1 if p >= avg else 0) << i
    return bits

def _hamming(a: int, b: int) -> int:
    """Calculate Hamming distance between two hashes"""
    return bin(a ^ b).count("1")

def _mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Calculate mean absolute difference between frames"""
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(a, b)))

def _hsv_hist(img: np.ndarray, bins: int = HIST_BINS) -> np.ndarray:
    """Calculate HSV histogram for image comparison"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = []
    for ch in range(3):
        h = cv2.calcHist([hsv], [ch], None, [bins], [0, 256])
        h = cv2.normalize(h, h).flatten()
        hist.append(h)
    return np.concatenate(hist)

def _hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Calculate histogram distance"""
    return float(1.0 - cv2.compareHist(a.astype('float32'), b.astype('float32'), cv2.HISTCMP_CORREL))

def _laplacian_sharpness(img) -> float:
    """Calculate image sharpness using Laplacian variance"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

def _brightness_score(img) -> float:
    """Calculate image brightness"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    return float(np.mean(v))

def _add_margin_box(x, y, w, h, frame_shape, margin_ratio=FACE_MARGIN):
    """Add margin around bounding box"""
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
    Analyze video to detect person, capture face, and detect shelf changes.
    
    KEY FIX: Captures BEFORE frame from BEFORE person enters, 
    and AFTER frame AFTER person leaves.
    
    Returns: (person_id, person_name, before_image_path, after_image_path)
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    
    print(f"📹 Analyzing video: {video_path}")
    
    before_path = str(CAPTURED_DIR / "before.jpg")
    after_path = str(CAPTURED_DIR / "after.jpg")
    face_path = str(FACES_DIR / f"face_{os.getpid()}.jpg")
    
    # State tracking
    clean_shelf_buffer: List[np.ndarray] = []  # Frames BEFORE person appears
    person_present = False
    person_ever_detected = False
    last_person_time = None
    face_samples: List[Tuple[float, float, float, np.ndarray]] = []
    prev_shelf: Optional[np.ndarray] = None
    before_hist: Optional[np.ndarray] = None
    hb: Optional[int] = None
    frame_count = 0
    frames_since_person_left = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_count += 1
        shelf_frame = _crop_roi(frame, shelf_roi)
        person_flag, person_box = _detect_person(frame)
        
        # Calculate motion in shelf ROI
        motion = 0.0
        if prev_shelf is not None:
            motion = _mean_abs_diff(shelf_frame, prev_shelf)
        prev_shelf = shelf_frame.copy()
        
        # === BEFORE PERSON ENTERS: Collect clean shelf frames ===
        if not person_flag and not person_ever_detected:
            # Only collect stable frames (low motion)
            if motion < MOTION_DIFF_THRESHOLD:
                clean_shelf_buffer.append(shelf_frame.copy())
                if len(clean_shelf_buffer) > STABILITY_BUFFER:
                    clean_shelf_buffer.pop(0)
        
        # === PERSON JUST APPEARED ===
        if person_flag and not person_present:
            print(f"👤 Person detected at frame {frame_count}")
            
            # Capture BEFORE frame from the clean buffer (BEFORE person entered)
            if clean_shelf_buffer:
                mid_idx = len(clean_shelf_buffer) // 2
                before = clean_shelf_buffer[mid_idx]
                print(f"   Using clean frame from buffer (index {mid_idx}/{len(clean_shelf_buffer)})")
            else:
                # Fallback: use current shelf frame (not ideal)
                before = shelf_frame
                print(f"   ⚠️  No clean buffer, using current frame")
            
            _write_img(before_path, before)
            before_hist = _hsv_hist(before)
            hb = _ahash(before_path)
            
            person_present = True
            person_ever_detected = True
            last_person_time = time.monotonic()
            print(f"📸 BEFORE frame captured (from before person entered)")
        
        # === WHILE PERSON IS PRESENT: Collect face samples ===
        if person_flag and person_present:
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
            frames_since_person_left = 0
        
        # === PERSON LEFT ===
        if not person_flag and person_present:
            frames_since_person_left += 1
            now = time.monotonic()
            
            if last_person_time is None:
                last_person_time = now
            
            # Wait the specified time after person left
            if now - last_person_time >= wait_seconds:
                print(f"👋 Person left at frame {frame_count}")
                print(f"   Waiting for shelf to stabilize...")
                
                # Continue reading frames to find a stable AFTER frame
                search_start = time.monotonic()
                stable_after_found = False
                
                while time.monotonic() - search_start < AFTER_MAX_SEARCH_SECONDS:
                    ret2, frame2 = cap.read()
                    if not ret2:
                        break
                    
                    frame_count += 1
                    shelf_frame2 = _crop_roi(frame2, shelf_roi)
                    
                    # Check if person returned
                    person_returned, _ = _detect_person(frame2)
                    if person_returned:
                        print(f"   ⚠️  Person returned, continuing to wait...")
                        last_person_time = time.monotonic()
                        continue
                    
                    # Write candidate after frame
                    _write_img(after_path, shelf_frame2)
                    
                    if hb is None or before_hist is None:
                        stable_after_found = True
                        break
                    
                    # Check if shelf changed from BEFORE
                    ha = _ahash(after_path)
                    after_hist = _hsv_hist(shelf_frame2)
                    
                    hamming_dist = _hamming(hb, ha)
                    hist_dist = _hist_distance(before_hist, after_hist)
                    
                    changed = (hamming_dist >= min_change_hamming) or (hist_dist >= min_hist_diff)
                    
                    if changed:
                        print(f"✅ AFTER frame captured (change detected: hamming={hamming_dist}, hist={hist_dist:.3f})")
                        stable_after_found = True
                        break
                
                if not stable_after_found:
                    print(f"⏱️  AFTER frame captured (timeout)")
                
                # Done processing this person
                break
    
    cap.release()
    print(f"✅ Video analysis complete ({frame_count} frames)")
    
    # === Process collected face samples ===
    person_id, person_name = None, None
    if face_samples:
        print(f"🔍 Analyzing {len(face_samples)} face samples")
        face_samples.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        best_crop = face_samples[0][3]
        _write_img(face_path, best_crop)
        print(f"💾 Best face saved: {face_path}")
        
        person_id, person_name = identify_face(face_path)
        if person_name:
            print(f"✅ Person identified: {person_name}")
        else:
            print(f"❓ Person not recognized")
    else:
        print(f"❌ No face samples collected")
    
    return person_id, person_name, before_path, after_path
