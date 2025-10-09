# app/face_identify.py
import os
import requests
from typing import Optional, Tuple
from dotenv import load_dotenv

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
TIMEOUT = (5, 20)

def identify_face(image_path: str) -> Tuple[Optional[str], Optional[str]]:
    detect_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/detect?returnFaceId=true"
    try:
        with open(image_path, "rb") as image_data:
            resp = requests.post(detect_url, headers=HEADERS_STREAM, data=image_data, timeout=TIMEOUT)
        resp.raise_for_status()
        detect_result = resp.json()
        if not isinstance(detect_result, list) or len(detect_result) == 0:
            return None, None

        best = max(
            detect_result,
            key=lambda d: (d.get("faceRectangle", {}).get("width", 0) * d.get("faceRectangle", {}).get("height", 0))
        )
        face_id = best.get("faceId")
        if not face_id:
            return None, None

        identify_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/identify"
        payload = {
            "personGroupId": PERSON_GROUP_ID,
            "faceIds": [face_id],
            "maxNumOfCandidatesReturned": 1,
            "confidenceThreshold": 0.6
        }
        resp_id = requests.post(identify_url, headers=HEADERS_JSON, json=payload, timeout=TIMEOUT)
        resp_id.raise_for_status()
        result = resp_id.json()
        if not isinstance(result, list) or len(result) == 0:
            return None, None

        candidates = result[0].get("candidates", [])
        if not candidates:
            return None, None

        person_id = candidates[0].get("personId")
        if not person_id:
            return None, None

        person_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/persongroups/{PERSON_GROUP_ID}/persons/{person_id}"
        resp_p = requests.get(person_url, headers={"Ocp-Apim-Subscription-Key": AZURE_FACE_KEY}, timeout=TIMEOUT)
        resp_p.raise_for_status()
        name = resp_p.json().get("name")
        return person_id, name
    except Exception:
        return None, None