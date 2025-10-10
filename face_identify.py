# app/face_identify.py

import os
import requests
from typing import Optional, Tuple
from dotenv import load_dotenv
import cv2
import numpy as np

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

def preprocess_face_image(image_path: str) -> bytes:
    """
    Preprocess face image to improve Azure Face API detection:
    - Resize if too large (max 6MB, 4096x4096)
    - Ensure minimum size (36x36)
    - Enhance contrast and brightness
    - Convert to JPEG with quality 95
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Cannot read image: {image_path}")
    
    h, w = img.shape[:2]
    
    # Ensure minimum dimensions
    if w < 36 or h < 36:
        scale = max(36 / w, 36 / h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        h, w = img.shape[:2]
    
    # Resize if too large
    max_dim = 1920
    if w > max_dim or h > max_dim:
        scale = min(max_dim / w, max_dim / h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    
    # Enhance image quality
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    enhanced = cv2.merge([l, a, b])
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    
    # Encode as high-quality JPEG
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
    _, buffer = cv2.imencode('.jpg', enhanced, encode_param)
    return buffer.tobytes()

def identify_face(image_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Identify face using Azure Face API with improved error handling.
    Returns: (person_id, person_name) or (None, None) if no match
    """
    detect_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/detect?returnFaceId=true&recognitionModel=recognition_01&detectionModel=detection_01"
    
    try:
        # Preprocess image for better detection
        image_bytes = preprocess_face_image(image_path)
        
        # Detect face
        resp = requests.post(detect_url, headers=HEADERS_STREAM, data=image_bytes, timeout=TIMEOUT)
        resp.raise_for_status()
        detect_result = resp.json()
        
        if not isinstance(detect_result, list) or len(detect_result) == 0:
            print(f"❌ No face detected in {image_path}")
            return None, None
        
        # Select largest face
        best = max(
            detect_result,
            key=lambda d: (d.get("faceRectangle", {}).get("width", 0) * 
                          d.get("faceRectangle", {}).get("height", 0))
        )
        
        face_id = best.get("faceId")
        if not face_id:
            print(f"❌ No faceId returned from detection")
            return None, None
        
        print(f"✅ Face detected, faceId: {face_id[:8]}...")
        
        # Identify face against person group
        identify_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/identify"
        payload = {
            "personGroupId": PERSON_GROUP_ID,
            "faceIds": [face_id],
            "maxNumOfCandidatesReturned": 1,
            "confidenceThreshold": 0.5
        }
        
        resp_id = requests.post(identify_url, headers=HEADERS_JSON, json=payload, timeout=TIMEOUT)
        resp_id.raise_for_status()
        result = resp_id.json()
        
        if not isinstance(result, list) or len(result) == 0:
            print(f"❌ No identification result")
            return None, None
        
        candidates = result[0].get("candidates", [])
        if not candidates:
            print(f"❌ No matching candidates found")
            return None, None
        
        person_id = candidates[0].get("personId")
        confidence = candidates[0].get("confidence", 0.0)
        
        if not person_id:
            print(f"❌ No personId in candidate")
            return None, None
        
        print(f"✅ Match found with confidence: {confidence:.2f}")
        
        # Get person details
        person_url = f"{AZURE_FACE_ENDPOINT}face/v1.0/persongroups/{PERSON_GROUP_ID}/persons/{person_id}"
        resp_p = requests.get(person_url, headers={"Ocp-Apim-Subscription-Key": AZURE_FACE_KEY}, timeout=TIMEOUT)
        resp_p.raise_for_status()
        name = resp_p.json().get("name")
        
        print(f"✅ Identified person: {name} (ID: {person_id[:8]}...)")
        return person_id, name
        
    except requests.exceptions.RequestException as e:
        print(f"❌ API request error: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return None, None
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        return None, None

