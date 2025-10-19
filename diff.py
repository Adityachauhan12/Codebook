import sys
from PIL import Image
import cv2
from src.detector import detect_books
from src.ocr_azure import azure_ocr_extract
from src.utils import fuzzy_match
from ALL_BOOKS import ALL_BOOKS
import json

# Import or initialize your YOLO model here
# Example for ultralytics YOLO:
# from ultralytics import YOLO
# MODEL = YOLO('yolov8.pt')
MODEL = None  # TODO: Replace with actual model initialization

def crop_book(image, box):
    return image.crop(box)

def get_books(image_path):
    img = Image.open(image_path)
    boxes = detect_books(image_path)
    detected_books = []
    for box in boxes:
        crop = crop_book(img, box)
        text = azure_ocr_extract(crop)
        matches = fuzzy_match(text, ALL_BOOKS, cutoff=0.5)
        detected_books.append({"ocr": text, "matches": matches})
    return detected_books

def run_looped_video(video_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    while True:
        ret, frame = cap.read()
        if not ret:  # Restart video when it ends
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        # YOLO detection
        results = MODEL.predict(frame, imgsz=1280)
        for r in results:
            for box in r.boxes.xyxy.tolist():
                x1, y1, x2, y2 = map(int, box)
                crop = Image.fromarray(frame[y1:y2, x1:x2])
                text = azure_ocr_extract(crop)
                matches = fuzzy_match(text, ALL_BOOKS, cutoff=0.5)
                ocr_result = {"ocr": text, "matches": matches}
                print(ocr_result)

        # Optional: visualize detections
        cv2.imshow("Looped Detection", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

def diff(before_path, after_path):
    before_books = get_books(before_path)
    after_books = get_books(after_path)

    before_titles = set(sum([b["matches"] for b in before_books], []))
    after_titles = set(sum([b["matches"] for b in after_books], []))

    added = list(after_titles - before_titles)
    removed = list(before_titles - after_titles)

    result = {
        "added_books": added,
        "removed_books": removed,
    }
    print(json.dumps(result, indent=2))
    return result

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python detector.py --mode [loop|diff] <video_path>")
        sys.exit(1)

    mode = sys.argv[1]
    video_path = sys.argv[2]

    if mode == "loop":
        run_looped_video(video_path)
    else:
        print("Invalid mode. Only 'loop' is supported in this script.")
        sys.exit(1)
    
