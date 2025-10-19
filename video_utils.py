# src/video_utils.py
import cv2
import os
import numpy as np
from typing import Tuple, Optional

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def frame_gray_blur(frame, ksize=5):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (ksize, ksize), 0)

def sample_video_frames(video_path: str, sample_rate: int = 3):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    sampled = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % sample_rate == 0:
            sampled.append((idx, frame.copy()))
        idx += 1
    cap.release()
    return sampled

def compute_motion_signal(frames_with_idx, roi: Optional[Tuple[int,int,int,int]] = None):
    motions = []
    if len(frames_with_idx) < 2:
        return motions
    prev = frame_gray_blur(frames_with_idx[0][1])
    for i in range(1, len(frames_with_idx)):
        curr = frame_gray_blur(frames_with_idx[i][1])
        diff = cv2.absdiff(curr, prev)
        if roi:
            x,y,w,h = roi
            diff = diff[y:y+h, x:x+w]
        motion_value = float(np.sum(diff)) / (diff.shape[0]*diff.shape[1] + 1e-9)
        motions.append(motion_value)
        prev = curr
    return motions

def select_before_after(frames_with_idx, motions, pre_margin=3, post_margin=3):
    if len(motions) == 0:
        before_idx = 0
        after_idx = len(frames_with_idx)-1
        return frames_with_idx[before_idx][1], frames_with_idx[after_idx][1]
    max_i = int(np.argmax(motions)) + 1
    before_sel = max(0, max_i - pre_margin)
    after_sel = min(len(frames_with_idx)-1, max_i + post_margin)
    before_frame = frames_with_idx[before_sel][1]
    after_frame = frames_with_idx[after_sel][1]
    return before_frame, after_frame

def save_frame(img_bgr, out_path):
    cv2.imwrite(out_path, img_bgr)

def video_to_before_after(
    video_path: str,
    out_dir: str = "examples",
    sample_rate: int = 5,
    roi: Optional[Tuple[int,int,int,int]] = None,
    pre_margin: int = 3,
    post_margin: int = 3,
    debug: bool = False
):
    ensure_dir(out_dir)
    frames_with_idx = sample_video_frames(video_path, sample_rate=sample_rate)
    if len(frames_with_idx) < 2:
        raise RuntimeError("Video too short or sampling rate too high; not enough frames.")
    motions = compute_motion_signal(frames_with_idx, roi=roi)
    before_frame, after_frame = select_before_after(frames_with_idx, motions, pre_margin, post_margin)
    before_path = os.path.join(out_dir, "before.jpg")
    after_path = os.path.join(out_dir, "after.jpg")
    save_frame(before_frame, before_path)
    save_frame(after_frame, after_path)
    if debug:
        import json
        debug_data = {"sampled_indices": [int(fi) for fi,_ in frames_with_idx], "motions": [float(m) for m in motions]}
        with open(os.path.join(out_dir, "motion_debug.json"), "w") as f:
            json.dump(debug_data, f, indent=2)
    return before_path, after_path
