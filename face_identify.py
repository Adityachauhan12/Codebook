# faceidentity.py
import os
import io
import cv2
import json
import tempfile
import requests
from typing import Optional, Dict, Any, Tuple
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

def _clean_endpoint(url: str) -> str:
    return (url or "").rstrip("/")

AZURE_FACE_ENDPOINT = _clean_endpoint(os.getenv("AZURE_FACE_ENDPOINT"))  # e.g., https://6eface.cognitiveservices.azure.com
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

def _detect_faces_image(image_bytes: bytes):
    params = {
        "returnFaceId": "true",
        "recognitionModel": "recognition_04",
        "detectionModel": "detection_03",
        "returnFaceAttributes": "qualityForRecognition",
    }
    r = requests.post(DETECT_URL, headers=headers_octet, params=params, data=image_bytes, timeout=15)
    # Try to expose Face error details on failure
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = {"message": r.text}
        raise requests.HTTPError(json.dumps(detail), response=r)
    return r.json()

def _encode_jpg(bgr) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr)
    return buf.tobytes() if ok else b""

def _extract_face_frame_from_video(video_bytes: bytes, max_frames: int = 90, stride: int = 5) -> Optional[bytes]:
    # Write to temp file for OpenCV
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
        tmp.write(video_bytes)
        tmp.flush()
        cap = cv2.VideoCapture(tmp.name)
        if not cap.isOpened():
            return None
        frame_idx = 0
        candidate_img = None
        while frame_idx < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % stride == 0:
                # Optional: resize to width <= 1280 for faster upload
                h, w = frame.shape[:2]
                if w > 1280:
                    scale = 1280.0 / w
                    frame = cv2.resize(frame, (int(w*scale), int(h*scale)))
                jpg = _encode_jpg(frame)
                # Call Face Detect; return the first frame that has a face
                try:
                    faces = _detect_faces_image(jpg)
                    if faces:
                        cap.release()
                        return jpg
                except requests.HTTPError:
                    # If Face returns 400 for this frame, try next frame
                    pass
                # Keep a fallback (first processed)
                if candidate_img is None:
                    candidate_img = jpg
            frame_idx += 1
        cap.release()
        # Fallback: return any frame if none had faces detected
        return candidate_img

def identify_person_bytes(image_bytes: bytes) -> Dict[str, Any]:
    faces = _detect_faces_image(image_bytes)
    if not faces:
        return {"identified": False, "reason": "no_face"}
    faces = [f for f in faces if (f.get("faceAttributes", {}).get("qualityForRecognition") or "").lower() in ["high", "medium"]]
    if not faces:
        return {"identified": False, "reason": "low_quality_face"}
    best = _pick_best_face(faces)
    face_id = best["faceId"]
    body = {
        "personGroupId": PERSON_GROUP_ID,
        "faceIds": [face_id],
        "maxNumOfCandidatesReturned": 1,
        "confidenceThreshold": IDENTIFY_CONFIDENCE_THRESHOLD,
    }
    ident = requests.post(IDENTIFY_URL, headers=headers_json, json=body, timeout=15)
    if ident.status_code >= 400:
        try:
            detail = ident.json()
        except Exception:
            detail = {"message": ident.text}
        raise requests.HTTPError(json.dumps(detail), response=ident)
    out = ident.json()
    if not out or not out[0].get("candidates"):
        return {"identified": False, "reason": "no_match", "faceRectangle": best.get("faceRectangle")}
    cand = out[0]["candidates"][0]
    person_id = cand["personId"]
    confidence = cand["confidence"]
    person = requests.get(f"{GET_PERSON_URL}/{person_id}", headers={"Ocp-Apim-Subscription-Key": AZURE_FACE_KEY}, timeout=10)
    person.raise_for_status()
    pdata = person.json()
    return {
        "identified": True,
        "person": {"id": person_id, "name": pdata.get("name"), "userData": pdata.get("userData")},
        "confidence": confidence,
        "faceRectangle": best.get("faceRectangle"),
    }

app = FastAPI()

@app.post("/identify")
async def identify(file: UploadFile = File(...)):
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()
        ctype = (file.content_type or "").lower()
        # If it's a video, extract a representative face frame
        if filename.endswith((".mp4", ".mov", ".m4v", ".avi")) or "video/" in ctype:
            img_bytes = _extract_face_frame_from_video(raw)
            if not img_bytes:
                return JSONResponse({"identified": False, "reason": "no_frame_or_no_face_in_video"}, status_code=400)
        else:
            img_bytes = raw
        result = identify_person_bytes(img_bytes)
        return JSONResponse(result, status_code=200)
    except requests.HTTPError as e:
        # Surface Azure Face error details
        try:
            detail = json.loads(str(e))
        except Exception:
            detail = {"error": str(e)}
        return JSONResponse({"error": detail}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
