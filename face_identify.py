import os
import requests
from typing import Optional, Tuple
from dotenv import load_dotenv
import cv2

load_dotenv()

AZURE_FACE_ENDPOINT = os.getenv("AZURE_FACE_ENDPOINT")
AZURE_FACE_KEY = os.getenv("AZURE_FACE_KEY")
PERSON_GROUP_ID = os.getenv("PERSON_GROUP_ID", "indigo-employees")

if not AZURE_FACE_ENDPOINT or not AZURE_FACE_KEY:
    raise RuntimeError("Missing AZURE_FACE_ENDPOINT or AZURE_FACE_KEY in environment")

if not AZURE_FACE_ENDPOINT.endswith("/"):
    AZURE_FACE_ENDPOINT += "/"

HEADERS_STREAM = {"Ocp-Apim-Subscription-Key": AZURE_FACE_KEY, "Content-Type": "application/octet-stream"}
HEADERS_JSON = {"Ocp-Apim-Subscription-Key": AZURE_FACE_KEY, "Content-Type": "application/json"}
TIMEOUT = (10, 30)

def identify_face(image_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Identify face using Azure Face API (recognition_01 to match your person group).
    Returns: (person_id, person_name) or (None, None)
    """
    detect_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/detect?returnFaceId=true&recognitionModel=recognition_01&detectionModel=detection_01"
    try:
        img = cv2.imread(image_path)
        if img is None:
            print(f"❌ Cannot read image: {image_path}")
            return None, None

        h, w = img.shape[:2]
        if w < 200 or h < 200:
            scale = max(200 / w, 200 / h)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

        _, buffer = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        image_bytes = buffer.tobytes()

        resp = requests.post(detect_url, headers=HEADERS_STREAM, data=image_bytes, timeout=TIMEOUT)
        resp.raise_for_status()
        detected = resp.json()
        if not isinstance(detected, list) or not detected:
            print("❌ No face detected")
            return None, None

        best = max(detected, key=lambda d: (d.get("faceRectangle", {}).get("width", 0) * d.get("faceRectangle", {}).get("height", 0)))
        face_id = best.get("faceId")
        if not face_id:
            print("❌ No faceId returned")
            return None, None

        identify_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/identify"
        payload = {
            "personGroupId": PERSON_GROUP_ID,
            "faceIds": [face_id],
            "maxNumOfCandidatesReturned": 1,
            "confidenceThreshold": 0.4
        }
        r2 = requests.post(identify_url, headers=HEADERS_JSON, json=payload, timeout=TIMEOUT)
        r2.raise_for_status()
        result = r2.json()
        if not result or not result[0].get("candidates"):
            print("❌ No matching candidates found")
            return None, None

        person_id = result[0]["candidates"][0]["personId"]
        person_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/persongroups/{PERSON_GROUP_ID}/persons/{person_id}"
        r3 = requests.get(person_url, headers={"Ocp-Apim-Subscription-Key": AZURE_FACE_KEY}, timeout=TIMEOUT)
        r3.raise_for_status()
        name = r3.json().get("name")
        return person_id, name
    except requests.exceptions.RequestException as e:
        print(f"❌ API error: {e}")
        return None, None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None, None
