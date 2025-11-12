# gcs_utils.py
import os, json, re, uuid
from datetime import datetime
from typing import Optional, List, Tuple, Dict
from google.cloud import storage

from dotenv import load_dotenv
load_dotenv()


# ----------------- ENV -----------------
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")  # REQUIRED
GCS_BASE_FOLDER = os.getenv("GCS_BASE_FOLDER", "Grooming-Results")

if not GCS_BUCKET_NAME:
    raise RuntimeError("GCS_BUCKET_NAME is not set. Put it in .env or env vars.")

_client = storage.Client()
_bucket = _client.bucket(GCS_BUCKET_NAME)

# ----------------- Internals -----------------
def _yyyymmdd(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%Y%m%d")

def _hhmmss(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%H%M%S")

def _slugify(s: Optional[str]) -> str:
    s = (s or "Unknown").strip()
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s)
    return s or "Unknown"

def _upload_text(content: str, dest_blob: str, content_type: str = "application/json", metadata: Optional[Dict[str, str]] = None) -> str:
    blob = _bucket.blob(dest_blob)
    if metadata:
        blob.metadata = metadata
    blob.upload_from_string(content, content_type=content_type)
    return f"gs://{GCS_BUCKET_NAME}/{dest_blob}"

def _upload_bytes(data: bytes, dest_blob: str, content_type: str, metadata: Optional[Dict[str, str]] = None) -> str:
    blob = _bucket.blob(dest_blob)
    if metadata:
        blob.metadata = metadata
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{GCS_BUCKET_NAME}/{dest_blob}"

def _download_text(src_blob: str) -> str:
    return _bucket.blob(src_blob).download_as_text()

def _download_json(src_blob: str) -> dict:
    return json.loads(_download_text(src_blob))

def _exists(blob_name: str) -> bool:
    return _bucket.blob(blob_name).exists()

def _list_by_prefix(prefix: str) -> List[str]:
    names = [b.name for b in _bucket.list_blobs(prefix=prefix)]
    names.sort()
    names.reverse()
    return names

# ----------------- Public APIs -----------------
def upload_image_bytes(img_bytes: bytes, iga_code: str, kind: str = "frame", crew_name: Optional[str] = None, now: Optional[datetime] = None) -> str:
    """
    Upload image bytes to:
      BASE/DATE/images/<IGA>/<kind>_<HHMMSS>.jpg
    Adds metadata: crew_name, iga_code.
    """
    dt = now or datetime.now()
    date_str = _yyyymmdd(dt)
    time_str = _hhmmss(dt)
    iga = _slugify(iga_code)
    blob_path = f"{GCS_BASE_FOLDER}/{date_str}/images/{iga}/{kind}_{time_str}.jpg"
    return _upload_bytes(
        img_bytes,
        blob_path,
        content_type="image/jpeg",
        metadata={"crew_name": crew_name or "Unknown", "iga_code": iga_code or "Unknown", "kind": kind},
    )

def append_event_to_crew_log(item: dict, crew_name: Optional[str], iga_code: Optional[str], now: Optional[datetime] = None) -> Tuple[str, int]:
    """
    Save to per-crew JSON:
      BASE/DATE/crew_<IGA>_<HHMMSS>.json with {assessments:[...]}
    Returns (blob_path, assessment_number)
    """
    dt = now or datetime.now()
    date_str = _yyyymmdd(dt)
    time_str = _hhmmss(dt)
    iga = _slugify(iga_code)
    folder = f"{GCS_BASE_FOLDER}/{date_str}"
    file_name = f"crew_{iga}_{time_str}.json"
    blob_path = f"{folder}/{file_name}"

    if _exists(blob_path):
        doc = _download_json(blob_path)
    else:
        doc = {
            "iga_code": iga_code or "Unknown",
            "crew_name": crew_name or "Unknown",
            "date": date_str,
            "assessments": []
        }

    num = len(doc.get("assessments", [])) + 1
    doc["assessments"].append({**item, "assessment_number": num})
    _upload_text(json.dumps(doc, indent=2), blob_path, content_type="application/json",
                 metadata={"crew_name": crew_name or "Unknown", "iga_code": iga_code or "Unknown", "type": "per_crew_log"})
    return blob_path, num

def create_ticket(item: dict, log_name: str = "grooming_tickets_log.json") -> str:
    """
    Append an entry to BASE/grooming_tickets_log.json
    Returns ticket_id
    """
    log_blob = f"{GCS_BASE_FOLDER}/{log_name}"
    log = _download_json(log_blob) if _exists(log_blob) else []
    ticket_id = f"SNTKT-{str(uuid.uuid4())[:8]}"
    log.append({"ticket_id": ticket_id, "timestamp": datetime.now().isoformat(), **item})
    _upload_text(json.dumps(log, indent=2), log_blob, metadata={"type": "tickets_log"})
    return ticket_id
 
def latest_assessments_today(iga_code: str, now: Optional[datetime] = None) -> list:
    """Return assessments[] from the latest per-crew JSON today (if exists)."""
    dt = now or datetime.now()
    date_str = _yyyymmdd(dt)
    iga = _slugify(iga_code)
    prefix = f"{GCS_BASE_FOLDER}/{date_str}/crew_{iga}_"
    names = _list_by_prefix(prefix)
    if not names:
        return []
    latest = names[0]
    doc = _download_json(latest)
    return doc.get("assessments", [])
# gcs_utils.py

def upload_grooming_result_text(
    result_text: str,
    crew_name: Optional[str],
    iga_code: Optional[str],
    image_gcs_path: Optional[str] = None,
    parsed: Optional[dict] = None,           # <-- NEW (optional)
    now: Optional[datetime] = None
) -> str:
    dt = now or datetime.now()
    date_str = _yyyymmdd(dt)
    time_str = _hhmmss(dt)
    iga = _slugify(iga_code)

    blob_path = f"{GCS_BASE_FOLDER}/{date_str}/results/{iga}/grooming_result_{iga}_{time_str}.json"

    # Safely pick parsed fields if present
    assessment = None
    score = None
    issues = []
    details = {}
    scores = {}

    if isinstance(parsed, dict):
        assessment = parsed.get("assessment")
        score = parsed.get("score")
        issues = parsed.get("issues") or []
        details = parsed.get("details") or {}
        scores = parsed.get("scores") or {}

    payload = {
        "timestamp": datetime.now().isoformat(),
        "iga_code": iga_code or "Unknown",
        "crew_name": crew_name or "Unknown",
        "image_gcs_path": image_gcs_path,
        "result_text": result_text,           # raw Gemini output (unchanged)
        # ------- NEW convenience fields for Insights --------
        "assessment": assessment,             # COMPLIANT / NON-COMPLIANT (if available)
        "score": score,                       # number or null
        "issues": issues,                     # list[str]
        "details": details,                   # map
        "scores": scores,                     # map
        "parsed": parsed if isinstance(parsed, dict) else None,  # full parsed blob
    }

    return _upload_text(
        json.dumps(payload, indent=2),
        blob_path,
        metadata={
            "crew_name": crew_name or "Unknown",
            "iga_code": iga_code or "Unknown",
            "type": "grooming_result"
        }
    )
    return _upload_text(json.dumps(payload, indent=2), blob_path, metadata={"crew_name": crew_name or "Unknown", "iga_code": iga_code or "Unknown", "type": "grooming_result"})
