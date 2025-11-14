from __future__ import annotations

import os
import re
import base64
import shutil

from datetime import datetime, timedelta, date as _date
from typing import Optional, Dict, Any, Tuple, List
from gcs_utils import _list_by_prefix, _download_json

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Load environment variables
load_dotenv()

import config  # noqa: E402

# ---------------- Grooming analysis (Gemini) ----------------
from grooming_utils import (  # noqa: E402
    check_grooming as run_grooming_analysis,
    check_grooming_from_video,
)

# ---------------- Regex helpers to parse Gemini text ----------------
_rx = {
    "overall_assessment": re.compile(r"^Overall\s*Assessment\s*:\s*(.+)$", re.I | re.M),
    "overall_score": re.compile(
        r"(?:Overall|Total|Score)\s*:\s*([0-9]+)(?:\.[0-9]+)?\s*/\s*10", re.I
    ),
    "uniform_score": re.compile(r"Uniform\s*:\s*([0-9]+)(?:\.[0-9]+)?\s*/\s*3", re.I),
    "nails_score": re.compile(r"Nails\s*:\s*([0-9]+)(?:\.[0-9]+)?\s*/\s*1", re.I),
    "hairstyle_score": re.compile(r"Hairstyle\s*:\s*([0-9]+)(?:\.[0-9]+)?\s*/\s*2", re.I),
    "makeup_score": re.compile(r"Makeup\s*:\s*([0-9]+)(?:\.[0-9]+)?\s*/\s*2", re.I),
    "accessories_score": re.compile(
        r"Accessories\s*:\s*([0-9]+)(?:\.[0-9]+)?\s*/\s*2", re.I
    ),
    # NEW: Detect (NOT VISIBLE) markers for visibility-based scoring
    "uniform_not_visible": re.compile(r"Uniform.*\(NOT\s+VISIBLE\)", re.I),
    "hairstyle_not_visible": re.compile(r"Hairstyle.*\(NOT\s+VISIBLE\)", re.I),
    "makeup_not_visible": re.compile(r"Makeup.*\(NOT\s+VISIBLE\)", re.I),
    "nails_not_visible": re.compile(r"Nails.*\(NOT\s+VISIBLE\)", re.I),
    "accessories_not_visible": re.compile(r"Accessories.*\(NOT\s+VISIBLE\)", re.I),
    # Detail observations
    "uniform_detail": re.compile(r"-\s*Uniform\s*:\s*(.+)$", re.I | re.M),
    "hairstyle_detail": re.compile(r"-\s*Hairstyle\s*:\s*(.+)$", re.I | re.M),
    "makeup_detail": re.compile(r"-\s*Makeup\s*:\s*(.+)$", re.I | re.M),
    "nails_detail": re.compile(r"-\s*Nails\s*:\s*(.+)$", re.I | re.M),
    "acc_detail": re.compile(r"-\s*Accessories\s*:\s*(.+)$", re.I | re.M),
    "issues_block": re.compile(r"Issues\s*Found\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
    "reco_block": re.compile(r"Recommendations\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
}


def _extract(pat: re.Pattern, text: str) -> Tuple[bool, str]:
    m = pat.search(text or "")
    return (m is not None, m.group(1).strip() if m else "")


def _num(s: str) -> Optional[int]:
    """Convert to integer only, no decimals."""
    try:
        return int(float(s))
    except Exception:
        return None


def _lines_to_list(block: str) -> List[str]:
    out: List[str] = []
    for ln in (block or "").splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.startswith("-"):
            out.append(t.lstrip("- ").strip())
        elif t[0:2].isdigit() or t.startswith(("1.", "2.", "3.", "4.", "5.")):
            out.append(t.split(".", 1)[-1].strip() if "." in t else t)
    return out


def _normalize_assessment(a: Optional[str]) -> Optional[str]:
    if a is None:
        return None
    a = a.strip().upper()
    a = a.replace("NONCOMPLIANT", "NON-COMPLIANT")
    return a if a in ("COMPLIANT", "NON-COMPLIANT") else None


def _parse_text_to_ui(text: str, name: Optional[str], iga: Optional[str]) -> Dict[str, Any]:
    """
    Parse Gemini text to UI dict, handling NOT VISIBLE items with full marks.
    All scores are INTEGER, no decimals.
    
    Logic:
    - If item is marked (NOT VISIBLE): award full marks, list in issues
    - If item is visible but violates: deduct marks per Gemini
    - If item is visible and compliant: award full marks
    """
    ok_assess, a_text = _extract(_rx["overall_assessment"], text)
    ok_score, s_text = _extract(_rx["overall_score"], text)

    score = _num(s_text) if ok_score else None
    assessment = _normalize_assessment(a_text if ok_assess else None)

    # Extract category scores and check for NOT VISIBLE markers
    cats: Dict[str, int] = {}
    not_visible_flags: Dict[str, bool] = {}
    
    category_configs = [
        ("uniform", "uniform_score", "uniform_not_visible", 3),
        ("hairstyle", "hairstyle_score", "hairstyle_not_visible", 2),
        ("makeup", "makeup_score", "makeup_not_visible", 2),
        ("nails", "nails_score", "nails_not_visible", 1),
        ("accessories", "accessories_score", "accessories_not_visible", 2),
    ]
    
    for cat_name, score_key, visible_key, max_val in category_configs:
        # Check if item is marked as NOT VISIBLE
        is_not_visible = _rx[visible_key].search(text or "") is not None
        not_visible_flags[cat_name] = is_not_visible
        
        # Extract score from Gemini response
        ok, val = _extract(_rx[score_key], text)
        extracted_score = _num(val) if ok else None
        
        # Scoring logic:
        # - If NOT VISIBLE: use full marks (no penalty)
        # - If visible and score found: use extracted score
        # - If visible but no score: default to 0 (violation detected)
        if is_not_visible:
            cats[cat_name] = max_val
        else:
            cats[cat_name] = extracted_score if extracted_score is not None else 0

    # Extract details/observations
    details: Dict[str, str] = {}
    for key, rx_key in [
        ("uniform", "uniform_detail"),
        ("hairstyle", "hairstyle_detail"),
        ("makeup", "makeup_detail"),
        ("nails", "nails_detail"),
        ("accessories", "acc_detail"),
    ]:
        _, v = _extract(_rx[rx_key], text)
        details[key] = v

    # Calculate overall score if not provided by Gemini
    if score is None:
        score = int(sum(v or 0 for v in cats.values()))
        if score > 10:
            score = 10

    # Determine compliance based on score
    if score is not None:
        assessment = "COMPLIANT" if score >= 7 else "NON-COMPLIANT"
    elif assessment is None:
        assessment = "NON-COMPLIANT"

    # Extract issues and recommendations
    ok_i, block_i = _extract(_rx["issues_block"], text)
    ok_r, block_r = _extract(_rx["reco_block"], text)

    return {
        "person": {"name": name or "", "iga_code": iga or ""},
        "assessment": assessment,
        "score": int(score),  # Ensure integer
        "scores": cats,
        "details": details,
        "issues": _lines_to_list(block_i) if ok_i else [],
        "recommendations": _lines_to_list(block_r) if ok_r else [],
        "_metadata": {"not_visible": not_visible_flags},  # Audit trail for debugging
    }


# ---------------- GCS utils used by grooming routes ----------------
from gcs_utils import (  # noqa: E402
    upload_image_bytes,
    upload_grooming_result_text,
    append_event_to_crew_log,
    create_ticket,
)

# ---------------- App setup ----------------
app = FastAPI(title="Grooming Checks + Insights API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GroomingRequest(BaseModel):
    imageBase64: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None
    base: Optional[str] = None
    department: Optional[str] = None


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": datetime.now().isoformat()}


# ---------------- Grooming (image) ----------------
@app.post("/check-grooming")
async def check_grooming_endpoint(payload: GroomingRequest):
    if not payload.imageBase64 or len(payload.imageBase64) < 10:
        return JSONResponse({"error": "Invalid image"}, status_code=400)

    try:
        b64 = payload.imageBase64.split(",")[-1]
        img_bytes = base64.b64decode(b64)

        # Run Gemini grooming analysis
        report = run_grooming_analysis(b64)
        parsed = _parse_text_to_ui(report, payload.crewName, payload.igaCode)

        # Persist artifacts/results in GCS
        img_path = upload_image_bytes(img_bytes, payload.igaCode, "image", payload.crewName)
        upload_grooming_result_text(report, payload.crewName, payload.igaCode, img_path, parsed=parsed)
        append_event_to_crew_log(
            {"type": "image", "parsed": parsed, "image_path": img_path},
            payload.crewName,
            payload.igaCode,
        )
        create_ticket(
            {"type": "image", "igaCode": payload.igaCode, "crewName": payload.crewName, "image_path": img_path}
        )

        return {"status": "ok", "result": parsed}
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------- Grooming (video) ----------------
@app.post("/check-grooming-video")
async def check_grooming_video(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...),
):
    # Accept only .mp4 files
    if not video.filename.lower().endswith(".mp4"):
        return JSONResponse({"error": "Only .mp4 files are allowed"}, status_code=400)

    # Check file size BEFORE saving (limit: 20 MB)
    # Read in chunks to avoid loading entire file into memory
    MAX_SIZE = 20 * 1024 * 1024  # 20 MB
    size = 0
    chunk_size = 1024 * 1024  # 1 MB chunks
    
    # Reset to beginning
    await video.seek(0)
    
    # Check size by reading chunks
    while True:
        chunk = await video.read(chunk_size)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_SIZE:
            return JSONResponse({"error": "Video size must be <= 20 MB"}, status_code=400)
    
    # Reset file pointer after size check
    await video.seek(0)

    try:
        videos_dir = os.path.join("uploads", "videos")
        os.makedirs(videos_dir, exist_ok=True)
        video_path = os.path.join(videos_dir, video.filename)
        
        with open(video_path, "wb") as f:
            shutil.copyfileobj(video.file, f)

        full_text = check_grooming_from_video(video_path, name, iga_code)
        parsed = _parse_text_to_ui(full_text, name, iga_code)

        return {"status": "ok", "result": parsed}
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------- The 3 requested APIs ----------------
from dashboard_service import get_insights, get_info, search_people  # noqa: E402


def _resolve_range(dateFrom: Optional[_date], dateTo: Optional[_date], days: Optional[int]):
    today = datetime.utcnow().date()
    if days and int(days) > 0:
        d = int(days)
        _from = today - timedelta(days=d - 1)
        _to = today
        return _from, _to
    _from = dateFrom or (today - timedelta(days=6))
    _to = dateTo or today
    return _from, _to


@app.get("/v1/insights")
async def insights(
    dateFrom: Optional[_date] = Query(None),
    dateTo: Optional[_date] = Query(None),
    days: Optional[int] = Query(None, description="custom duration in days; e.g., 7 for last 7 days"),
    page: int = 1,
    pageSize: int = 25,
):
    _from, _to = _resolve_range(dateFrom, dateTo, days)
    return get_insights(_from, _to, page, pageSize)


@app.get("/v1/info")
async def info_box(
    dateFrom: Optional[_date] = Query(None),
    dateTo: Optional[_date] = Query(None),
    days: Optional[int] = Query(None),
):
    _from, _to = _resolve_range(dateFrom, dateTo, days)
    return get_info(_from, _to)


@app.get("/v1/search")
async def search_endpoint(
    q: str = Query("", description="search by IGA code or crew name (case-insensitive)"),
    dateFrom: Optional[_date] = Query(None),
    dateTo: Optional[_date] = Query(None),
    days: Optional[int] = Query(None),
    page: int = 1,
    pageSize: int = 25,
):
    _from, _to = _resolve_range(dateFrom, dateTo, days)
    return search_people(_from, _to, q, page, pageSize)


# Fixed individual analysis endpoint
@app.get("/v1/individual-analysis")
async def individual_analysis(
    igaCode: str = Query(..., description="IGA code"),
    crewName: str = Query(..., description="Crew name"),
    dateFrom: str = Query(..., description="Start date in YYYY-MM-DD"),
    dateTo: str = Query(..., description="End date in YYYY-MM-DD"),
):
    try:
        # Parse dates
        start_date = datetime.strptime(dateFrom, "%Y-%m-%d").date()
        end_date = datetime.strptime(dateTo, "%Y-%m-%d").date()

        if start_date > end_date:
            return {"error": "dateFrom cannot be after dateTo"}

        # Import the internal function directly
        from dashboard_service import _load_records, Filters
        
        # Load all records in date range (same as insights API)
        filters = Filters(date_from=start_date, date_to=end_date)
        all_records = _load_records(filters)

        # Filter for this specific crew (case-insensitive)
        iga_normalized = igaCode.strip().upper()
        crew_normalized = crewName.strip().upper()
        
        crew_records = [
            r for r in all_records 
            if (r.get("iga_code") or "").strip().upper() == iga_normalized 
            and (r.get("crew_name") or "").strip().upper() == crew_normalized
        ]

        if not crew_records:
            return {"error": "No assessments found for this crew in given date range"}

        # Summary statistics
        total = len(crew_records)
        compliant = sum(1 for r in crew_records if r.get("assessment") == "COMPLIANT")
        noncompliant = total - compliant
        passrate = f"{round(compliant / total * 100, 2)}%" if total > 0 else "0%"

        # Category-wise non-compliance counts
        categories = ["uniform", "hairstyle", "makeup", "nails", "accessories"]
        category_counts = {c.capitalize(): 0 for c in categories}
        for r in crew_records:
            issues = r.get("issues") or []
            for issue in issues:
                issue_lower = (issue or "").lower()
                for c in categories:
                    if c in issue_lower:
                        category_counts[c.capitalize()] += 1
                        break

        # Trend for date range
        trend_map = {}
        for r in crew_records:
            date_obj = r.get("date")
            if not date_obj:
                continue
            
            key = date_obj.strftime("%Y-%m-%d")
            if key not in trend_map:
                trend_map[key] = {"compliant": 0, "nonCompliant": 0}
            
            if r.get("assessment") == "COMPLIANT":
                trend_map[key]["compliant"] += 1
            else:
                trend_map[key]["nonCompliant"] += 1

        # Pad trend for all dates in range
        trend_list = []
        current_date = start_date
        while current_date <= end_date:
            key = current_date.strftime("%Y-%m-%d")
            trend_list.append({
                "date": key,
                "compliant": trend_map.get(key, {}).get("compliant", 0),
                "nonCompliant": trend_map.get(key, {}).get("nonCompliant", 0)
            })
            current_date += timedelta(days=1)

        return {
            "crew": {
                "igaCode": igaCode,
                "name": crewName,
            },
            "summary": {
                "totalAssessments": total,
                "compliant": compliant,
                "nonCompliant": noncompliant,
                "passRate": passrate,
            },
            "nonComplianceByCategory": category_counts,
            "trend": trend_list,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


# ---------------- Entrypoint ----------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=True)
