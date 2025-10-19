# src/watch_and_diff.py
import os, time, json, cv2, numpy as np
from collections import deque
from ultralytics import YOLO
from . import config
from .video_utils import ensuredir, framegrayblur
from .detector import diffimages
from .utils import iou

def parse_roi(roi_str, w, h):
    if not roi_str:
        return (0, 0, w, h)
    x,y,ww,hh = [int(v) for v in roi_str.split(",")]
    x = max(0, min(x, w-1)); y = max(0, min(y, h-1))
    ww = max(1, min(ww, w-x)); hh = max(1, min(hh, h-y))
    return (x,y,ww,hh)

class EntryExitWatcher:
    def __init__(self, outdir):
        ensuredir(outdir)
        self.outdir = outdir
        self.person_model = YOLO(config.PERSON_MODEL)
        self.before_path = os.path.join(outdir, "before.jpg")
        self.after_path  = os.path.join(outdir, "after.jpg")

    def person_present(self, frame_bgr):
        results = self.person_model(frame_bgr, conf=config.PRESENCE_CONF, imgsz=config.IMGSIZE, device=config.DEVICE)
        if not results:
            return False
        r = results[0]
        names = getattr(r, "names", getattr(self.person_model, "names", {}))
        present = False
        for b in r.boxes:
            clsid = int(b.cls[0].item()) if hasattr(b,"cls") else None
            cname = names.get(clsid) if isinstance(names, dict) else None
            if cname == "person":
                present = True
                break
        return present

    def motion_value(self, gray_prev, gray_curr, roi):
        x,y,w,h = roi
        a = gray_prev[y:y+h, x:x+w]
        b = gray_curr[y:y+h, x:x+w]
        diff = cv2.absdiff(a, b)
        return float(np.sum(diff)) / (diff.shape[0]*diff.shape[1] + 1e-9)

    def run(self, video_path):
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Empty video")

        H, W = frame.shape[:2]
        roi = parse_roi(config.ROI, W, H)
        buf_len = max(1, int(config.PRE_BUFFER_SEC * config.FPS_HINT))
        empty_buffer = deque(maxlen=buf_len)

        state = "IDLE"
        present_consec = 0
        absent_consec = 0
        last_gray = framegrayblur(frame)

        t_last_change = time.time()

        before_saved = False
        after_saved  = False
        cooldown_ok  = False

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            gray = framegrayblur(frame)
            mv = self.motion_value(last_gray, gray, roi)
            last_gray = gray

            present = self.person_present(frame)

            if present:
                present_consec += 1
                absent_consec = 0
            else:
                absent_consec += 1
                present_consec = 0

            # maintain buffer of empty frames only
            if not present:
                empty_buffer.append(frame.copy())

            if state == "IDLE":
                # confirm entry
                if present_consec >= config.PRESENCE_CONSEC:
                    # save last empty frame as BEFORE
                    if len(empty_buffer) > 0:
                        cv2.imwrite(self.before_path, empty_buffer[-1])
                        before_saved = True
                    state = "IN_INTERACTION"
                    t_last_change = time.time()
            elif state == "IN_INTERACTION":
                # confirm exit + stillness
                if absent_consec >= config.ABSENCE_CONSEC:
                    cooldown_ok = (mv < config.MOTION_STILL_THRESH) and (time.time() - t_last_change >= config.POST_COOLDOWN_SEC)
                    if cooldown_ok:
                        cv2.imwrite(self.after_path, frame)
                        after_saved = True
                        break

        cap.release()
        if not before_saved or not after_saved:
            raise RuntimeError("Failed to capture clean before/after frames")
        return self.before_path, self.after_path

def summarize_titles(diff_result):
    def pick_title(d):
        if d.get("accepted"):
            return d["accepted"]["title"]
        if d.get("matches"):
            return d["matches"][0]["title"]
        return (d.get("ocr") or "").strip()
    taken  = [pick_title(d) for d in diff_result.get("taken", [])]
    placed = [pick_title(d) for d in diff_result.get("placed", [])]
    return {"taken": taken, "placed": placed}

def main(video_path, outdir):
    watcher = EntryExitWatcher(outdir)
    before_path, after_path = watcher.run(video_path)
    diff_result = diffimages(before_path, after_path, outdir)
    summary = summarize_titles(diff_result)
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default=config.OUTPUTDIR)
    args = ap.parse_args()
    main(args.video, args.out)
