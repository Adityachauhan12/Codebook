# faceidentity.py
import os
import io
import requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

AZURE_FACE_ENDPOINT = os.getenv("AZURE_FACE_ENDPOINT")  # e.g., https://6eface.cognitiveservices.azure.com/
AZURE_FACE_KEY = os.getenv("AZURE_FACE_KEY")
PERSON_GROUP_ID = os.getenv("PERSON_GROUP_ID", "indigo-employees")
IDENTIFY_CONFIDENCE_THRESHOLD = float(os.getenv("IDENTIFY_CONFIDENCE_THRESHOLD", "0.6"))

DETECT_URL = f"{AZURE_FACE_ENDPOINT}/face/v1.0/detect"
IDENTIFY_URL = f"{AZURE_FACE_ENDPOINT}/face/v1.0/identify"
GET_PERSON_URL = f"{AZURE_FACE_ENDPOINT}/face/v1.0/persongroups/{PERSON_GROUP_ID}/persons"

headers_octet = {
    "Ocp-Apim-Subscription-Key": AZURE_FACE_KEY,
    "Content-Type": "application/octet-stream",
}
headers_json = {
    "Ocp-Apim-Subscription-Key": AZURE_FACE_KEY,
    "Content-Type": "application/json",
}

def _pick_best_face(faces):
    # Prefer highest qualityForRecognition, then largest area
    def quality_rank(q):
        order = {"high": 3, "medium": 2, "low": 1}
        return order.get((q or "").lower(), 0)

    best = None
    best_score = -1
    for f in faces:
        rect = f.get("faceRectangle", {})
        area = rect.get("width", 0) * rect.get("height", 0)
        q = (f.get("faceAttributes", {}).get("qualityForRecognition") or "").lower()
        score = quality_rank(q) * 1_000_000 + area
        if score > best_score:
            best_score = score
            best = f
    return best

def identify_person_bytes(image_bytes: bytes) -> Dict[str, Any]:
    # 1) Detect with quality attribute
    params = {
        "returnFaceId": "true",
        "recognitionModel": "recognition_04",
        "detectionModel": "detection_03",
        "returnFaceAttributes": "qualityForRecognition",
    }
    det = requests.post(DETECT_URL, headers=headers_octet, params=params, data=image_bytes, timeout=15)
    det.raise_for_status()
    faces = det.json()
    if not faces:
        return {"identified": False, "reason": "no_face"}

    # Filter out very low-quality faces
    faces = [f for f in faces if (f.get("faceAttributes", {}).get("qualityForRecognition") or "").lower() in ["high", "medium"]]
    if not faces:
        return {"identified": False, "reason": "low_quality_face"}

    best = _pick_best_face(faces)
    face_id = best["faceId"]

    # 2) Identify against PersonGroup
    body = {
        "personGroupId": PERSON_GROUP_ID,
        "faceIds": [face_id],
        "maxNumOfCandidatesReturned": 1,
        "confidenceThreshold": IDENTIFY_CONFIDENCE_THRESHOLD,
    }
    ident = requests.post(IDENTIFY_URL, headers=headers_json, json=body, timeout=15)
    ident.raise_for_status()
    out = ident.json()
    if not out or not out[0].get("candidates"):
        return {
            "identified": False,
            "reason": "no_match",
            "faceRectangle": best.get("faceRectangle"),
        }

    cand = out[0]["candidates"][0]
    person_id = cand["personId"]
    confidence = cand["confidence"]

    # 3) Resolve person name
    person = requests.get(f"{GET_PERSON_URL}/{person_id}", headers={"Ocp-Apim-Subscription-Key": AZURE_FACE_KEY}, timeout=10)
    person.raise_for_status()
    pdata = person.json()

    return {
        "identified": True,
        "person": {
            "id": person_id,
            "name": pdata.get("name"),
            "userData": pdata.get("userData"),
        },
        "confidence": confidence,
        "faceRectangle": best.get("faceRectangle"),
    }

app = FastAPI()

@app.post("/identify")
async def identify(file: UploadFile = File(...)):
    image_bytes = await file.read()
    try:
        result = identify_person_bytes(image_bytes)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
