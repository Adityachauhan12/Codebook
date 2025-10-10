from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
import numpy as np
import cv2
import os
import uuid
import math
import requests
import base64
import io
# import pygame
import time
from ultralytics import YOLO
from dotenv import load_dotenv
from pydantic import BaseModel

class RTSPRequest(BaseModel):
    url: str

load_dotenv()


app = FastAPI()

# ==== Configuration ====
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
AZURE_KEY = os.getenv("AZURE_KEY")
SARVAM_ENDPOINT = os.getenv("SARVAM_ENDPOINT")
SARVAM_KEY = os.getenv("SARVAM_KEY")
PERSON_GROUP_ID = "indigo-employees"
OUTPUT_DIR = "detected_faces"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ==== Azure Headers ====
headers_stream = {
    "Ocp-Apim-Subscription-Key": AZURE_KEY,
    "Content-Type": "application/octet-stream"
}
headers_json = {
    "Ocp-Apim-Subscription-Key": AZURE_KEY,
    "Content-Type": "application/json"
}

# Init pygame mixer
#pygame.mixer.init()

# ==== TTS Speaker ====
def speak_welcome(name):
    text = f"Welcome {name}"
    response = requests.post(
        SARVAM_ENDPOINT,
        headers={"api-subscription-key": SARVAM_KEY},
        json={"text": text, "target_language_code": "en-IN"},
    )
    if response.status_code == 200:
        print(f"🔊 (Skipped playback) Welcome message for: {name}")
    else:
        print(f"❌ TTS API Error: {response.status_code}")
    # if response.status_code == 200:
    #     audio_base64 = response.json().get("audios", [None])[0]
    #     if audio_base64:
    #         audio_bytes = base64.b64decode(audio_base64)
    #         audio_buffer = io.BytesIO(audio_bytes)
    #         pygame.mixer.music.load(audio_buffer)
    #         pygame.mixer.music.play()
    #         while pygame.mixer.music.get_busy():
    #             time.sleep(0.1)
    #     else:
    #         print("❌ No audio found in response.")
    # else:
    #     print(f"❌ TTS API Error: {response.status_code}")


# ==== Helper Functions ====
def get_center(x1, y1, x2, y2):
    return ((x1 + x2) // 2, (y1 + y2) // 2)

def is_new_entry(center, seen_centers, threshold=60):
    for prev_center in seen_centers:
        if math.dist(center, prev_center) < threshold:
            return False
    return True

def identify_face(image_path):
    detect_url = f"{AZURE_ENDPOINT}face/v1.0/detect?returnFaceId=true"
    with open(image_path, 'rb') as image_data:
        response = requests.post(detect_url, headers=headers_stream, data=image_data)
    detect_result = response.json()

    if not detect_result or "faceId" not in detect_result[0]:
        print("❌ No face detected or detection error.")
        return None

    face_id = detect_result[0]["faceId"]

    identify_url = f"{AZURE_ENDPOINT}face/v1.0/identify"
    identify_data = {
        "personGroupId": PERSON_GROUP_ID,
        "faceIds": [face_id],
        "maxNumOfCandidatesReturned": 1,
        "confidenceThreshold": 0.6
    }
    response = requests.post(identify_url, headers=headers_json, json=identify_data)
    result = response.json()

    if result and result[0]["candidates"]:
        person_id = result[0]["candidates"][0]["personId"]
        person_url = f"{AZURE_ENDPOINT}face/v1.0/persongroups/{PERSON_GROUP_ID}/persons/{person_id}"
        response = requests.get(person_url, headers={"Ocp-Apim-Subscription-Key": AZURE_KEY})
        person_info = response.json()
        return person_info.get("name")

    return None

# ==== Load YOLO Model ====
model_path = os.path.join("faced_model", "model.pt")
model = YOLO(model_path)

seen_faces = []

# @app.post("/upload-frame")
# async def receive_frame(file: UploadFile = File(...)):
#     contents = await file.read()
#     img_np = np.frombuffer(contents, np.uint8)
#     frame = cv2.imdecode(img_np, cv2.IMREAD_COLOR)

#     results = model(frame)[0]

#     for box in results.boxes:
#         conf = float(box.conf[0])
#         x1, y1, x2, y2 = map(int, box.xyxy[0])
#         center = get_center(x1, y1, x2, y2)

#         if conf > 0.6 and is_new_entry(center, seen_faces):
#             seen_faces.append(center)
#             face_crop = frame[y1:y2, x1:x2]
#             filename = os.path.join(OUTPUT_DIR, f"face_{uuid.uuid4()}.jpg")
#             cv2.imwrite(filename, face_crop)
#             print(f"🆕 New face saved: {filename}")

#             name = identify_face(filename)
#             if name:
#                 print(f"👋 Welcome {name}!")
#                 speak_welcome(name)
#             else:
#                 print("👤 Unknown person.")

#     return JSONResponse(content={"status": "frame received and processed"})

# @app.post("/upload-video")
# async def receive_video(file: UploadFile = File(...)):
#     contents = await file.read()
#     video_path = os.path.join(OUTPUT_DIR, f"uploaded_{uuid.uuid4()}.mp4")
#     with open(video_path, "wb") as f:
#         f.write(contents)

#     cap = cv2.VideoCapture(video_path)
#     frame_count = 0
#     detected_faces = 0
#     while True:
#         ret, frame = cap.read()
#         if not ret:
#             break
#         frame_count += 1
#         results = model(frame)[0]
#         for box in results.boxes:
#             conf = float(box.conf[0])
#             x1, y1, x2, y2 = map(int, box.xyxy[0])
#             center = get_center(x1, y1, x2, y2)
#             if conf > 0.6 and is_new_entry(center, seen_faces):
#                 seen_faces.append(center)
#                 face_crop = frame[y1:y2, x1:x2]
#                 filename = os.path.join(OUTPUT_DIR, f"face_{uuid.uuid4()}.jpg")
#                 cv2.imwrite(filename, face_crop)
#                 detected_faces += 1
#                 print(f"🆕 New face saved: {filename}")
#                 name = identify_face(filename)
#                 if name:
#                     print(f"👋 Welcome {name}!")
#                     speak_welcome(name)
#                 else:
#                     print("👤 Unknown person.")
#     cap.release()
#     os.remove(video_path)
#     return JSONResponse(content={"status": "video processed", "frames": frame_count, "faces_detected": detected_faces})

# @app.post("/stream-frame")
# async def stream_frame(file: UploadFile = File(...)):
#     contents = await file.read()
#     img_np = np.frombuffer(contents, np.uint8)
#     frame = cv2.imdecode(img_np, cv2.IMREAD_COLOR)

#     if frame is None:
#         return JSONResponse(status_code=400, content={"error": "Invalid image"})

#     results = model(frame)[0]
#     faces_in_frame = 0

#     for box in results.boxes:
#         conf = float(box.conf[0])
#         x1, y1, x2, y2 = map(int, box.xyxy[0])
#         center = get_center(x1, y1, x2, y2)
#         if conf > 0.6 and is_new_entry(center, seen_faces):
#             seen_faces.append(center)
#             face_crop = frame[y1:y2, x1:x2]
#             filename = os.path.join(OUTPUT_DIR, f"face_{uuid.uuid4()}.jpg")
#             cv2.imwrite(filename, face_crop)
#             faces_in_frame += 1
#             print(f"🆕 New face saved: {filename}")
#             name = identify_face(filename)
#             if name:
#                 print(f"👋 Welcome {name}!")
#                 speak_welcome(name)
#             else:
#                 print("👤 Unknown person.")

#     return JSONResponse(content={"status": "frame processed", "faces_detected": faces_in_frame})

@app.post("/stream-rtsp")
async def stream_rtsp(request: RTSPRequest):
    url = request.url
    cap = cv2.VideoCapture(url)

    if not cap.isOpened():
        return JSONResponse(status_code=400, content={"error": "Unable to open RTSP stream"})

    frame_count = 0
    detected_faces = 0
    seen_centers = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        results = model(frame)[0]

        for box in results.boxes:
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            center = get_center(x1, y1, x2, y2)

            if conf > 0.6 and is_new_entry(center, seen_centers):
                seen_centers.append(center)
                face_crop = frame[y1:y2, x1:x2]
                filename = os.path.join(OUTPUT_DIR, f"face_{uuid.uuid4()}.jpg")
                cv2.imwrite(filename, face_crop)
                detected_faces += 1

                print(f"🆕 New face saved: {filename}")
                name = identify_face(filename)
                if name:
                    print(f"👋 Welcome {name}!")
                    speak_welcome(name)
                else:
                    print("👤 Unknown person.")

        # Optional: Break after N frames or seconds to avoid long runs
        if frame_count > 300:
            break

    cap.release()
    return JSONResponse(content={
        "status": "RTSP stream processed",
        "frames": frame_count,
        "faces_detected": detected_faces
    })

@app.get("/")
def root():
    return {"message": "Camera service is running"}
