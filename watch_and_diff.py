# src/watch_and_diff.py
import os, time, json, cv2, numpy as np
from collections import deque
from ultralytics import YOLO
from . import config
from .video_utils import ensuredir, framegrayblur
from .detector import diffimages
import numpy as np

def parse_roi(roi_str, w, h):
    if not roi_str: return (0,0,w,h)
    x,y,ww,hh = [int(v) for v in roi_str.split(",")]
    x = max(0, min(x, w-1)); y = max(0, min(y, h-1))
    ww = max(1, min(ww, w-x)); hh = max(1, min(hh, h-y))
    return (x,y,ww,hh)

class EntryExitWatcher:
    def __init__(self, outdir):
        ensuredir(outdir)
        self.outdir = outdir
        self.before_path = os.path.join(outdir, "before.jpg")
        self.after_path  = os.path.join(outdir, "after.jpg")
        self.person_model = YOLO(config.PERSON_MODEL)

    def person_present(self, frame):
        results = self.person_model(frame, conf=config.PRESENCE_CONF, imgsz=config.IMGSIZE, device=config.DEVICE)
        if not results: return False
        r = results[0]
        names = getattr(r, "names", getattr(self.person_model, "names", {}))
        for b in r.boxes:
            clsid = int(b.cls[0].item()) if hasattr(b,"cls") else None
            if isinstance(names, dict) and names.get(clsid) == "person":
                return True
        return False

    def motion_value(self, prev_g, curr_g, roi):
        x,y,w,h = roi
        a = prev_g[y:y+h, x:x+w]
        b = curr_g[y:y+h, x:x+w]
        diff = cv2.absdiff(a, b)
        return float(np.sum(diff)) / (diff.shape[0]*diff.shape[1] + 1e-9)

    def run(self, video_path):
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened(): raise RuntimeError(f"Cannot open video: {video_path}")
        ok, frame = cap.read()
        if not ok: raise RuntimeError("Empty video")

        H, W = frame.shape[:2]
        roi = parse_roi(config.ROI, W, H)
        buf_len = max(1, int(config.PRE_BUFFER_SEC * config.FPS_HINT))
        empty_buf = deque(maxlen=buf_len)

        present_consec = absent_consec = 0
        state = "IDLE"
        last_gray = framegrayblur(frame)
        t_last = time.time()

        while True:
            ok, frame = cap.read()
            if not ok: break
            gray = framegrayblur(frame)
            mv = self.motion_value(last_gray, gray, roi)
            last_gray = gray

            present = self.person_present(frame)
            if present: present_consec, absent_consec = present_consec+1, 0
            else:       absent_consec, present_consec = absent_consec+1, 0
            if not present: empty_buf.append(frame.copy())

            if state == "IDLE" and present_consec >= config.PRESENCE_CONSEC:
                if len(empty_buf): cv2.imwrite(self.before_path, empty_buf[-1])
                state, t_last = "IN", time.time()

            if state == "IN" and absent_consec >= config.ABSENCE_CONSEC:
                if mv < config.MOTION_STILL_THRESH and (time.time()-t_last) >= config.POST_COOLDOWN_SEC:
                    cv2.imwrite(self.after_path, frame)
                    break

        cap.release()
        return self.before_path, self.after_path

def main(video, outdir):
    watcher = EntryExitWatcher(outdir)
    before_path, after_path = watcher.run(video)
    result = diffimages(before_path, after_path, outdir)
    # Titles only
    def pick(d):
        return (d.get("accepted") or {}).get("title") or (d.get("matches") or [{}])[0].get("title") or (d.get("ocr") or "").strip()
    summary = {"taken":[pick(d) for d in result.get("taken",[])],
               "placed":[pick(d) for d in result.get("placed",[])]}
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default="examples")
    args = ap.parse_args()
    main(args.video, args.out)
