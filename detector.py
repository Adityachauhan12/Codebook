# src/detector.py
import os, sys, json, time
import cv2
from PIL import Image
from ultralytics import YOLO

# allow "src" absolute imports if run as a script
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
from src.video_utils import video_to_before_after, ensure_dir
from src.ocr_azure import ocr_crop
from src.utils import iou, preprocess_for_ocr, fuzzy_match
from ALL_BOOKS import ALL_BOOKS

MODEL = YOLO(config.YOLO_MODEL)

def pad_bbox(x1, y1, x2, y2, img_w, img_h, pad_frac):
    w = x2 - x1; h = y2 - y1
    px = int(w * pad_frac); py = int(h * pad_frac)
    nx1 = max(0, int(x1 - px)); ny1 = max(0, int(y1 - py))
    nx2 = min(img_w, int(x2 + px)); ny2 = min(img_h, int(y2 + py))
    return nx1, ny1, nx2, ny2

def detect_books_on_image(image_path, imgsz=None):
    img = Image.open(image_path)
    img_w, img_h = img.size
    results = MODEL(
        image_path,
        conf=config.CONF_THRESH,
        imgsz=imgsz or config.IMG_SIZE,
        device=config.DEVICE
    )
    boxes = []
    if not results:
        return boxes
    r = results[0]
    names = getattr(r, "names", getattr(MODEL, "names", {}))
    for b in r.boxes:
        xyxy = b.xyxy[0].tolist()
        x1,y1,x2,y2 = [int(v) for v in xyxy]
        conf = float(b.conf[0].item())
        cls_id = int(b.cls[0].item()) if hasattr(b, "cls") and b.cls is not None else None
        cls_name = names.get(cls_id) if isinstance(names, dict) else None
        # optional class filter
        if config.CLASS_NAME and cls_name and cls_name != config.CLASS_NAME:
            continue
        x1,y1,x2,y2 = pad_bbox(x1,y1,x2,y2, img_w, img_h, config.PADDING)
        crop = img.crop((x1,y1,x2,y2))
        boxes.append({
            "bbox": [x1,y1,x2,y2],
            "conf": conf,
            "crop": crop
        })
    return boxes

def ocr_and_match(pil_img):
    pil_img = preprocess_for_ocr(pil_img)
    text = ocr_crop(pil_img)
    matches = fuzzy_match(text, ALL_BOOKS, topk=config.TOPK_MATCHES)
    accepted = matches[0] if (matches and matches[0]["score"] >= config.FUZZY_ACCEPT_THRESH) else None
    return {"ocr": text, "matches": matches, "accepted": accepted}

def annotate(img_path, detections, out_path):
    im = cv2.imread(img_path)
    for d in detections:
        x1,y1,x2,y2 = d["bbox"]
        cv2.rectangle(im, (x1,y1), (x2,y2), (0,255,0), 2)
        label = d.get("accepted", {}).get("title") if d.get("accepted") else f"{d['conf']:.2f}"
        if label:
            cv2.putText(im, label[:24], (x1, max(12, y1-6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1, cv2.LINE_AA)
    cv2.imwrite(out_path, im)

def pair_by_iou(befores, afters):
    matched = []
    used_after = set()
    for i, b in enumerate(befores):
        best_j, best_iou = None, 0.0
        for j, a in enumerate(afters):
            if j in used_after:
                continue
            iou_val = iou(b["bbox"], a["bbox"])
            if iou_val > best_iou:
                best_iou, best_j = iou_val, j
        if best_j is not None and best_iou >= config.IOU_MATCH_THRESH:
            matched.append((i, best_j, best_iou))
            used_after.add(best_j)
    before_unmatched = [idx for idx in range(len(befores)) if all(idx != m[0] for m in matched)]
    after_unmatched  = [idx for idx in range(len(afters))  if all(idx != m[1] for m in matched)]
    return matched, before_unmatched, after_unmatched

def diff_images(before_path, after_path, out_dir):
    ensure_dir(out_dir)
    before = detect_books_on_image(before_path)
    after  = detect_books_on_image(after_path)
    # OCR + match
    for d in before:
        d.update(ocr_and_match(d["crop"]))
    for d in after:
        d.update(ocr_and_match(d["crop"]))
    # Pair and summarize
    matched, b_un, a_un = pair_by_iou(before, after)
    result = {
        "before_count": len(before),
        "after_count": len(after),
        "matched_pairs": [
            {
                "before": {k:v for k,v in before[i].items() if k != "crop"},
                "after":  {k:v for k,v in after[j].items()  if k != "crop"},
                "iou": round(iou(before[i]["bbox"], after[j]["bbox"]), 4)
            } for (i,j,_) in matched
        ],
        "taken": [{k:v for k,v in before[i].items() if k != "crop"} for i in b_un],
        "placed": [{k:v for k,v in after[j].items()  if k != "crop"} for j in a_un]
    }
    # Save annotated images for visual inspection
    annotate(before_path, before, os.path.join(out_dir, "before_annotated.jpg"))
    annotate(after_path,  after,  os.path.join(out_dir, "after_annotated.jpg"))
    return result

def run_diff(video_path):
    before_path, after_path = video_to_before_after(video_path, config.OUTPUT_DIR, debug=True, sample_rate=3)
    result = diff_images(before_path, after_path, config.OUTPUT_DIR)
    if config.SAVE_JSON:
        with open(os.path.join(config.OUTPUT_DIR, "diff_result.json"), "w") as f:
            json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))

# near the end of detector.py
def titles_only(diff_result):
    def pick(d):
        if d.get("accepted"): return d["accepted"]["title"]
        if d.get("matches"):  return d["matches"][0]["title"]
        return (d.get("ocr") or "").strip()
    return {
        "taken":  [pick(d) for d in diff_result.get("taken", [])],
        "placed": [pick(d) for d in diff_result.get("placed", [])],
    }


def run_loop(video_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    t0 = time.time()
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        # process current frame as a temporary image in memory
        tmp_path = os.path.join(config.OUTPUT_DIR, "_loop_tmp.jpg")
        ensure_dir(config.OUTPUT_DIR)
        cv2.imwrite(tmp_path, frame)
        boxes = detect_books_on_image(tmp_path)
        for b in boxes:
            b.update(ocr_and_match(b["crop"]))
            # print compact result each frame
            print({"ocr": b["ocr"], "accepted": b["accepted"], "conf": round(b["conf"], 3)})
        frame_idx += 1
        if frame_idx >= config.LOOP_MAX_FRAMES or (time.time() - t0) >= config.LOOP_MAX_SECONDS:
            break
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    cap.release()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["diff","loop"], default="diff")
    ap.add_argument("--video", required=True, help="Path to video file")
    args = ap.parse_args()
    if args.mode == "diff":
        run_diff(args.video)
    else:
        run_loop(args.video)
