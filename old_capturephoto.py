# capturephoto.py
import os
import time
import json
from datetime import datetime
from typing import Optional, Dict, Any

import cv2
from ultralytics import YOLO
from dotenv import load_dotenv

from bookfind import compare_shelf
from faceidentity import identify_person_bytes

load_dotenv()

# Video source
RTSP_URL = os.getenv("RTSP_URL", "")  # e.g., rtsp://user:pass@ip:554/stream
VIDEO_PATH = os.getenv("VIDEO_PATH", "")  # optional local file path

# Models
YOLO_PERSON_WEIGHTS = os.getenv("YOLO_PERSON_WEIGHTS", "yolov8n.pt")
PERSON_CONF_THRESHOLD = float(os.getenv("PERSON_CONF_THRESHOLD", "0.4"))

# Interaction timing
EXIT_GRACE_SECONDS = float(os.getenv("EXIT_GRACE_SECONDS", "2.5"))
FRAME_SAVE_DIR = os.getenv("FRAME_SAVE_DIR", "captures")
os.makedirs(FRAME_SAVE_DIR, exist_ok=True)

person_model = YOLO(YOLO_PERSON_WEIGHTS)

def _open_capture():
    if RTSP_URL:
        cap = cv2.VideoCapture(RTSP_URL)
    elif VIDEO_PATH:
        cap = cv2.VideoCapture(VIDEO_PATH)
    else:
        cap = cv2.VideoCapture(0)
    return cap

def _detect_person(frame) -> bool:
    res = person_model(frame, verbose=False)[0]
    for box in res.boxes:
        cls_id = int(box.cls[0]) if box.cls is not None else -1
        conf = float(box.conf[0])
        if cls_id == 0 and conf >= PERSON_CONF_THRESHOLD:  # class 0 = person in COCO
            return True
    return False

def _encode_jpg(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes() if ok else b""

def run():
    cap = _open_capture()
    if not cap.isOpened():
        print("Unable to open video source")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    no_person_frames_needed = int(EXIT_GRACE_SECONDS * fps)

    person_present = False
    no_person_count = 0

    before_frame = None
    face_frame = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        has_person = _detect_person(frame)

        if has_person:
            if not person_present:
                # Person just entered: capture BEFORE and FACE frames
                before_frame = frame.copy()
                face_frame = frame.copy()
            person_present = True
            no_person_count = 0
        else:
            if person_present:
                no_person_count += 1
                if no_person_count >= no_person_frames_needed:
                    # Person has exited: capture AFTER and process
                    after_frame = frame.copy()

                    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                    before_path = os.path.join(FRAME_SAVE_DIR, f"before_{ts}.jpg")
                    after_path = os.path.join(FRAME_SAVE_DIR, f"after_{ts}.jpg")
                    cv2.imwrite(before_path, before_frame)
                    cv2.imwrite(after_path, after_frame)

                    # Identify person
                    person_result = identify_person_bytes(_encode_jpg(face_frame))

                    # Books diff
                    shelf_result = compare_shelf(before_path, after_path)

                    record: Dict[str, Any] = {
                        "timestamp_utc": ts,
                        "person": person_result.get("person") if person_result.get("identified") else {
                            "id": None,
                            "name": None
                        },
                        "identified": person_result.get("identified"),
                        "confidence": person_result.get("confidence"),
                        "books_taken": shelf_result.get("books_taken", []),
                        "books_returned": shelf_result.get("books_returned", []),
                        "before_image": before_path,
                        "after_image": after_path,
                    }

                    print(json.dumps(record, ensure_ascii=False, indent=2))

                    # Save to file
                    out_path = os.path.join(FRAME_SAVE_DIR, f"event_{ts}.json")
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(record, f, ensure_ascii=False, indent=2)

                    # Reset state for next person
                    person_present = False
                    no_person_count = 0
                    before_frame = None
                    face_frame = None
            else:
                # Still no person
                pass

        # Optional: press 'q' to stop
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
