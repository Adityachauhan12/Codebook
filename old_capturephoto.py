import cv2
import time
from ultralytics import YOLO

# === CONFIGURATION ===
VIDEO_PATH = "C:/Users/ramja/Desktop/Indigo/survillance/IMG_9083.MOV"

OUTPUT_IMAGE_PATH = "after_exit_frame.jpg"
DETECTION_MODEL = "yolov8n.pt"  # Use yolov8s.pt for better accuracy
WAIT_TIME_SECONDS = 3
CONFIDENCE_THRESHOLD = 0.4

# Load YOLOv8 model
model = YOLO(DETECTION_MODEL)

def detect_person(frame):
    """Return True if at least one person is detected."""
    results = model(frame)[0]
    for box in results.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        if cls_id == 0 and conf > CONFIDENCE_THRESHOLD:  # Class 0 is person
            return True
    return False

def main():
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_interval = int(fps * WAIT_TIME_SECONDS)

    person_present = False
    exit_counter = 0
    save_frame = None

    frame_id = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_id += 1
        has_person = detect_person(frame)

        if has_person:
            person_present = True
            exit_counter = 0  # Reset
            save_frame = None
        else:
            if person_present:
                exit_counter += 1
                if exit_counter == frame_interval:
                    save_frame = frame.copy()
                    cv2.imwrite(OUTPUT_IMAGE_PATH, save_frame)
                    print(f"[✓] Person left. Frame saved after {WAIT_TIME_SECONDS}s: {OUTPUT_IMAGE_PATH}")
                    person_present = False
                    exit_counter = 0

    cap.release()
    print("✅ Processing complete.")

if __name__ == "__main__":
    main()
