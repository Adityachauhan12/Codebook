# src/config.py
import os
from dotenv import load_dotenv

load_dotenv()

AZURE_OCR_ENDPOINT = os.getenv("AZURE_OCR_ENDPOINT")
AZURE_OCR_KEY = os.getenv("AZURE_OCR_KEY")

YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
DEVICE = os.getenv("DEVICE", None)         # "cpu", "mps", or "cuda"
IMG_SIZE = int(os.getenv("IMG_SIZE", 1280))
CLASS_NAME = os.getenv("CLASS_NAME", "book")

CONF_THRESH = float(os.getenv("CONF_THRESH", 0.35))
PADDING = float(os.getenv("PADDING", 0.06))
IOU_MATCH_THRESH = float(os.getenv("IOU_MATCH_THRESH", 0.45))
FUZZY_ACCEPT_THRESH = float(os.getenv("FUZZY_ACCEPT_THRESH", 0.60))
TOPK_MATCHES = int(os.getenv("TOPK_MATCHES", 3))

OCR_ENABLED = os.getenv("OCR_ENABLED", "1") == "1"
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "examples")
SAVE_JSON = os.getenv("SAVE_JSON", "1") == "1"

# Person/ROI/capture settings
PERSON_MODEL = os.getenv("PERSON_MODEL", "yolov8n.pt")        # COCO model for 'person'
PRESENCE_CONF = float(os.getenv("PRESENCE_CONF", 0.35))       # person conf
PRESENCE_CONSEC = int(os.getenv("PRESENCE_CONSEC", 3))        # frames to confirm entry
ABSENCE_CONSEC  = int(os.getenv("ABSENCE_CONSEC", 8))         # frames to confirm exit
PRE_BUFFER_SEC  = float(os.getenv("PRE_BUFFER_SEC", 2.0))     # seconds of empty frames to buffer
POST_COOLDOWN_SEC = float(os.getenv("POST_COOLDOWN_SEC", 1.0))# extra stillness time
ROI = os.getenv("ROI", "")  # "x,y,w,h" for shelf area; empty=full frame
MOTION_STILL_THRESH = float(os.getenv("MOTION_STILL_THRESH", 2.5e-3))  # normalized sum-diff
FPS_HINT = int(os.getenv("FPS_HINT", 30))  # used for buffer sizing

