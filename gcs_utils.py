"""
GCS utilities for grooming assessment system

UPDATED WITH BASE SUPPORT:
- All save operations now include base and terminal information
- Base data flows from localStorage → Frontend → Backend → GCS
"""

import os
import json
import re
import uuid
from datetime import datetime
from typing import Optional, List, Tuple, Dict
from google.cloud import storage
from dotenv import load_dotenv

load_dotenv()

# ============= ENV =============
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")  # REQUIRED
GCS_BASE_FOLDER = os.getenv("GCS_BASE_FOLDER", "Grooming-Results")

if not GCS_BUCKET_NAME:
    raise RuntimeError("GCS_BUCKET_NAME is not set. Put it in .env or env vars.")

client = storage.Client()
bucket = client.bucket(GCS_BUCKET_NAME)


# ============= Helpers =============
def _yyyymmdd(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%Y%m%d")


def _hhmmss(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%H%M%S")


def _slugify(s: Optional[str]) -> str:
    s = (s or "Unknown").strip()
    s = re.sub(r"[^A-Za-z0-9-]", "_", s)
    return s or "Unknown"


def _upload_text(content: str, dest_blob: str, content_type: str = "application/json", metadata: Optional[Dict[str, str]] = None) -> str:
    """Upload text content to GCS."""
    blob = bucket.blob(dest_blob)
    if metadata:
        blob.metadata = metadata
    blob.upload_from_string(content, content_type=content_type)
    return f"gs://{GCS_BUCKET_NAME}/{dest_blob}"


def _upload_bytes(data: bytes, dest_blob: str, content_type: str = "application/octet-stream") -> str:
    """Upload binary data to GCS."""
    blob = bucket.blob(dest_blob)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{GCS_BUCKET_NAME}/{dest_blob}"


# ============= Public Functions =============

def upload_image_bytes(img_bytes: bytes, iga_code: Optional[str], media_type: str = "image", crew_name: Optional[str] = None) -> str:
    """
    Upload image/video bytes to GCS.
    
    Args:
        img_bytes: Binary data (image or video)
        iga_code: IGA code
        media_type: "image" or "video"
        crew_name: Crew member name
    
    Returns:
        GCS path where file was saved
    """
    slug_iga = _slugify(iga_code)
    slug_crew = _slugify(crew_name or "Unknown")
    
    ext = "jpg" if media_type == "image" else "webm"
    file_name = f"{slug_iga}_{slug_crew}_{_yyyymmdd()}_{_hhmmss()}.{ext}"
    
    content_type = "image/jpeg" if media_type == "image" else "video/webm"
    dest_blob = f"{GCS_BASE_FOLDER}/{media_type}s/{file_name}"
    
    return _upload_bytes(img_bytes, dest_blob, content_type)


def upload_grooming_result_text(
    text: str,
    crew_name: str,
    iga_code: str,
    media_path: str,
    parsed: Optional[Dict] = None,
    assessment_mode: str = "image",  # "image" or "video"
    base: Optional[str] = None,      # ⭐ NEW: Base location
    terminal: Optional[str] = None   # ⭐ NEW: Terminal
) -> str:
    """
    Save grooming assessment result to GCS.
    Now includes base and terminal information.
    
    Args:
        text: Raw AI analysis text
        crew_name: Crew member name
        iga_code: IGA code
        media_path: GCS path to image/video
        parsed: Parsed result dictionary
        base: Base location (e.g., "DEL", "BOM")
        terminal: Terminal (e.g., "T1", "T2")
    
    Returns:
        GCS path where result was saved
    """
    
    # Build result data with base information
    result_data = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "date": datetime.utcnow().date().isoformat(),
        "crew_name": crew_name,
        "iga_code": iga_code,
        "assessment_mode": assessment_mode,  # Track video vs image assessment
        "base": base or "UNKNOWN",          # ⭐ NEW
        "terminal": terminal or "UNKNOWN",  # ⭐ NEW
        "media_path": media_path,
        "raw_text": text,
        "assessment": parsed.get("assessment") if parsed else "NON-COMPLIANT",
        "score": parsed.get("score") if parsed else 0,
        "scores": parsed.get("scores") if parsed else {},
        "issues": parsed.get("issues") if parsed else [],
        "recommendations": parsed.get("recommendations") if parsed else [],
        "details": parsed.get("details") if parsed else {},
    }
    
    print(f"💾 Saving to GCS with base: {base}, terminal: {terminal}")
    
    # Generate GCS path
    # Format: Grooming-Results/YYYYMMDD/results/IGA_CODE/filename.json (matches actual GCP structure)
    now = datetime.utcnow()
    file_name = f"grooming_result_{_slugify(iga_code)}_{now.strftime('%H%M%S')}.json"
    gcs_path = f"{GCS_BASE_FOLDER}/{now.strftime('%Y%m%d')}/results/{_slugify(iga_code)}/{file_name}"
    
    print(f"📁 GCS path: {gcs_path}")
    print(f"📊 Assessment: {result_data['assessment']}, Score: {result_data['score']}")
    
    # Upload to GCS
    try:
        _upload_text(
            json.dumps(result_data, indent=2),
            gcs_path,
            content_type="application/json"
        )
        
        print(f"✅ Successfully saved assessment to GCS: {gcs_path}")
        print(f"🔍 Record contains: {len(result_data.get('issues', []))} issues")
        return gcs_path
        
    except Exception as e:
        print(f"❌ Error saving to GCS: {e}")
        raise


def append_event_to_crew_log(
    event_data: Dict,
    crew_name: str,
    iga_code: str
) -> None:
    """
    Append assessment event to crew's log file.
    Event data now includes base and terminal.
    
    Args:
        event_data: Event dictionary containing assessment details
        crew_name: Crew member name
        iga_code: IGA code
    """
    
    log_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "crew_name": crew_name,
        "iga_code": iga_code,
        "base": event_data.get("base", "UNKNOWN"),          # ⭐ NEW
        "terminal": event_data.get("terminal", "UNKNOWN"),  # ⭐ NEW
        "type": event_data.get("type"),
        "assessment": event_data.get("parsed", {}).get("assessment"),
        "score": event_data.get("parsed", {}).get("score"),
        "media_path": event_data.get("image_path") or event_data.get("video_path"),
    }
    
    # Append to crew log in GCS
    log_path = f"{GCS_BASE_FOLDER}/crew_logs/{_slugify(iga_code)}.jsonl"
    
    try:
        blob = bucket.blob(log_path)
        
        # Append to existing log
        existing_content = ""
        if blob.exists():
            existing_content = blob.download_as_text()
        
        new_content = existing_content + json.dumps(log_entry) + "\n"
        blob.upload_from_string(new_content, content_type="text/plain")
        
        print(f"✅ Appended to crew log: {log_path}")
        
    except Exception as e:
        print(f"❌ Error appending to crew log: {e}")


def create_ticket(ticket_data: Dict) -> None:
    """
    Create ticket for assessment.
    Ticket now includes base and terminal.
    
    Args:
        ticket_data: Ticket information dictionary
    """
    
    ticket = {
        "ticket_id": f"GROOM_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "iga_code": ticket_data.get("igaCode"),
        "crew_name": ticket_data.get("crewName"),
        "base": ticket_data.get("base", "UNKNOWN"),          # ⭐ NEW
        "terminal": ticket_data.get("terminal", "UNKNOWN"),  # ⭐ NEW
        "type": ticket_data.get("type"),
        "media_path": ticket_data.get("image_path") or ticket_data.get("video_path"),
        "status": "pending",
    }
    
    ticket_path = f"{GCS_BASE_FOLDER}/tickets/{ticket['ticket_id']}.json"
    
    try:
        _upload_text(
            json.dumps(ticket, indent=2),
            ticket_path,
            content_type="application/json"
        )
        
        print(f"✅ Created ticket: {ticket_path}")
        
    except Exception as e:
        print(f"❌ Error creating ticket: {e}")


# ============= List and Download (for dashboard) =============

def _list_by_prefix(prefix: str) -> List[storage.Blob]:
    """List all blobs with given prefix."""
    return list(bucket.list_blobs(prefix=prefix))


def _download_json(blob_name: str) -> Dict:
    """Download and parse JSON blob."""
    blob = bucket.blob(blob_name)
    content = blob.download_as_text()
    return json.loads(content)
