"""
FastAPI backend for grooming checks + insights API

UPDATED WITH BASE SUPPORT:
1. Video results persisted to GCS
2. Issues extracted and saved correctly
3. Category breakdown shows only 5 categories
4. Individual analysis recalculates fresh from GCS with proper percentages
5. Debug logging on all critical operations
6. ⭐ NEW: Base and terminal support from localStorage
7. ⭐ NEW: Base-wise insights APIs
8. ✅ FIXED: Category deduplication using sets
9. ✅ FIXED: recentAssessments list comprehension syntax
"""

from __future__ import annotations

import os
import re
import base64
import shutil
from datetime import datetime, timedelta, date as _date
from typing import Optional, Dict, Any, Tuple, List
from gcs_utils import _list_by_prefix, _download_json
from dashboard_service import _load_records, Filters as DashboardFilters, _issue_heading
from collections import defaultdict
from datetime import datetime, date as _date, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Load environment variables
load_dotenv()

import config  # noqa: E402

# ============= Grooming analysis (Gemini) =============

from grooming_utils import (  # noqa: E402
    check_grooming as run_grooming_analysis,
    check_grooming_from_video,
)

# ============= Regex helpers to parse Gemini text =============

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
    "uniform_not_visible": re.compile(r"(?:Uniform|Tunic|Scarf|Badge|Stockings).*(?:\(NOT\s+VISIBLE\)|\*\*NOT\s+VISIBLE\*\*)", re.I),
    "hairstyle_not_visible": re.compile(r"(?:Hairstyle|Hair).*(?:\(NOT\s+VISIBLE\)|\*\*NOT\s+VISIBLE\*\*)", re.I),
    "makeup_not_visible": re.compile(r"Makeup.*(?:\(NOT\s+VISIBLE\)|\*\*NOT\s+VISIBLE\*\*)", re.I),
    "nails_not_visible": re.compile(r"(?:Nails|Nail).*(?:\(NOT\s+VISIBLE\)|\*\*NOT\s+VISIBLE\*\*)", re.I),
    "accessories_not_visible": re.compile(r"(?:Accessories|Watch|Rings|Earrings|Bangles).*(?:\(NOT\s+VISIBLE\)|\*\*NOT\s+VISIBLE\*\*)", re.I),
    "uniform_detail": re.compile(r"-\s*Uniform\s*:\s*(.+)$", re.I | re.M),
    "hairstyle_detail": re.compile(r"-\s*Hairstyle\s*:\s*(.+)$", re.I | re.M),
    "makeup_detail": re.compile(r"-\s*Makeup\s*:\s*(.+)$", re.I | re.M),
    "nails_detail": re.compile(r"-\s*Nails\s*:\s*(.+)$", re.I | re.M),
    "acc_detail": re.compile(r"-\s*Accessories\s*:\s*(.+)$", re.I | re.M),
    "issues_block": re.compile(r"Issues\s+Found\s*:\s*(.+?)(?:\n\s*(?:Recommendations|Observations|$))", re.I | re.S),
    "reco_block": re.compile(r"Recommendations\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
    "observations_block": re.compile(r"Observations\s*:\s*(.+?)(?:\n\s*(?:Issues|$))", re.I | re.S),
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
    """
    Extract bullet points from text block.
    Handles various formats: dash (-), asterisk (*), numbers (1., 2., etc.)
    """
    out: List[str] = []
    if not block:
        return out

    for ln in block.splitlines():
        t = ln.strip()
        if not t:
            continue

        # Remove leading bullets/numbers
        if t.startswith("-"):
            t = t.lstrip("- ").strip()
        elif t.startswith("*"):
            t = t.lstrip("* ").strip()
        elif t.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
            parts = t.split(".", 1)
            t = parts[-1].strip() if len(parts) > 1 else t
        elif re.match(r"^\d+\)", t):
            t = re.sub(r"^\d+\)\s*", "", t)

        if t and len(t) > 2:
            out.append(t)

    return out

def _normalize_assessment(a: Optional[str]) -> Optional[str]:
    if a is None:
        return None
    a = a.strip().upper()
    a = a.replace("NONCOMPLIANT", "NON-COMPLIANT")
    return a if a in ("COMPLIANT", "NON-COMPLIANT") else None

def _extract_issues_from_text(text: str) -> List[str]:
    """
    ROBUST: Extract issues using MULTIPLE fallback methods.
    Guaranteed to find violations one way or another.
    """
    all_issues = []
    if not text:
        print("[DEBUG] Text is empty")
        return []

    print(f"[DEBUG] ============ ISSUE EXTRACTION START ============")
    print(f"[DEBUG] Text length: {len(text)} characters")

    # METHOD 1: Look for explicit "Issues Found:" block
    print(f"[DEBUG] METHOD 1: Trying 'Issues Found:' pattern...")
    issues_patterns = [
        r"Issues\s+Found\s*:\s*(.+?)(?:\n\s*(?:Recommendations|Observations|$))",
        r"Issues\s*:\s*(.+?)(?:\n\s*(?:Recommendations|Observations|$))",
        r"ISSUES\s*:\s*(.+?)(?:\n\s*(?:Recommendations|$))",
    ]

    for pattern_str in issues_patterns:
        pattern = re.compile(pattern_str, re.I | re.S)
        m = pattern.search(text)
        if m:
            block = m.group(1).strip()
            print(f"[DEBUG] Found matching pattern")
            print(f"[DEBUG] Block content (first 200 chars): {block[:200]}")
            issues = _lines_to_list(block)
            if issues:
                all_issues.extend(issues)
                print(f"[DEBUG] ✓ Extracted {len(issues)} issues from Issues Found")
                for i, issue in enumerate(issues, 1):
                    print(f"[DEBUG] {i}. {issue}")
            break

    # METHOD 2: Try "Observations:" section
    if not all_issues:
        print(f"[DEBUG] METHOD 2: No issues from 'Issues Found', trying 'Observations:'...")
        obs_patterns = [
            r"Observations\s*:\s*(.+?)(?:\n\s*(?:Issues|Recommendations|$))",
            r"OBSERVATIONS\s*:\s*(.+?)(?:\n\s*(?:Issues|$))",
        ]

        for pattern_str in obs_patterns:
            pattern = re.compile(pattern_str, re.I | re.S)
            m = pattern.search(text)
            if m:
                block = m.group(1).strip()
                print(f"[DEBUG] Found Observations block")
                print(f"[DEBUG] Block content (first 200 chars): {block[:200]}")
                obs_list = _lines_to_list(block)
                if obs_list:
                    all_issues.extend(obs_list)
                    print(f"[DEBUG] ✓ Extracted {len(obs_list)} observations")
                    for i, obs in enumerate(obs_list, 1):
                        print(f"[DEBUG] {i}. {obs}")
                break

    # METHOD 3: Infer violations from category scores
    if not all_issues:
        print(f"[DEBUG] METHOD 3: No explicit issues found, inferring from category scores...")
        category_scores = {
            "uniform": (_extract(_rx["uniform_score"], text), 3),
            "hairstyle": (_extract(_rx["hairstyle_score"], text), 2),
            "makeup": (_extract(_rx["makeup_score"], text), 2),
            "nails": (_extract(_rx["nails_score"], text), 1),
            "accessories": (_extract(_rx["accessories_score"], text), 2),
        }

        for cat_name, (score_match, max_score) in category_scores.items():
            ok, val = score_match
            if ok:
                score = _num(val)
                print(f"[DEBUG] Category '{cat_name}': Score = {score}/{max_score}")
                if score is not None and score < max_score:
                    violation = f"{cat_name.capitalize()} violation (Score: {score}/{max_score})"
                    all_issues.append(violation)
                    print(f"[DEBUG] ✓ Inferred violation: {violation}")

    # METHOD 4: Look for any category with score < max (last resort)
    if not all_issues:
        print(f"[DEBUG] METHOD 4: Last resort - scanning for any score < max...")
        score_pattern = re.compile(r"(\w+)\s*:\s*([0-9]+)\s*/\s*([0-9]+)", re.I)
        matches = score_pattern.findall(text)

        for cat_name, score_str, max_str in matches:
            try:
                score = int(score_str)
                max_score = int(max_str)
                print(f"[DEBUG] Found score: {cat_name} = {score}/{max_score}")
                if score < max_score:
                    violation = f"{cat_name.capitalize()} violation (Score: {score}/{max_score})"
                    all_issues.append(violation)
                    print(f"[DEBUG] ✓ Found violation: {violation}")
            except:
                pass

    print(f"[DEBUG] ============ FINAL RESULT ============")
    print(f"[DEBUG] Total issues extracted: {len(all_issues)}")
    if all_issues:
        for i, issue in enumerate(all_issues, 1):
            print(f"[DEBUG] ISSUE {i}: {issue}")
    else:
        print(f"[DEBUG] ⚠️ No issues found (may be COMPLIANT test)")
    print(f"[DEBUG] ============ EXTRACTION COMPLETE ============\n")

    return all_issues if all_issues else []

def _parse_text_to_ui(text: str, name: Optional[str], iga: Optional[str]) -> Dict[str, Any]:
    """
    Parse Gemini text to UI dict, handling NOT VISIBLE items with full marks.
    All scores are INTEGER, no decimals.
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
        is_not_visible = _rx[visible_key].search(text or "") is not None
        not_visible_flags[cat_name] = is_not_visible

        ok, val = _extract(_rx[score_key], text)
        extracted_score = _num(val) if ok else None

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

    # Determine compliance based on score (7+ is COMPLIANT)
    if score is not None:
        assessment = "COMPLIANT" if score >= 7 else "NON-COMPLIANT"
    elif assessment is None:
        assessment = "NON-COMPLIANT"

    # Extract issues and remove only asterisks, keep category labels
    issues = [issue.replace('**', '') for issue in _extract_issues_from_text(text)]

    # Extract recommendations
    ok_r, block_r = _extract(_rx["reco_block"], text)

    return {
        "person": {"name": name or "", "iga_code": iga or ""},
        "assessment": assessment,
        "score": int(score),
        "scores": cats,
        "details": details,
        "issues": issues,
        "recommendations": _lines_to_list(block_r) if ok_r else [],
        "_metadata": {"not_visible": not_visible_flags},
    }

# ============= GCS utils used by grooming routes =============

from gcs_utils import (  # noqa: E402
    upload_image_bytes,
    upload_grooming_result_text,
    append_event_to_crew_log,
    create_ticket,
)

# ============= App setup =============

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
    base: Optional[str] = None  # ⭐ NEW: Base location
    terminal: Optional[str] = None  # ⭐ NEW: Terminal
    department: Optional[str] = None

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": datetime.now().isoformat()}

# ============= Grooming (image) =============

@app.post("/check-grooming")
async def check_grooming_endpoint(payload: GroomingRequest):
    if not payload.imageBase64 or len(payload.imageBase64) < 10:
        return JSONResponse({"error": "Invalid image"}, status_code=400)

    try:
        # ⭐ Normalize IGA code to ensure proper format
        normalized_iga = _normalize_iga_code(payload.igaCode)
        
        # ⭐ Log received data
        print(f"📥 Received photo assessment:")
        print(f" IGA Code: {payload.igaCode} → {normalized_iga}")
        print(f" Name: {payload.crewName}")
        print(f" Base: {payload.base}")
        print(f" Terminal: {payload.terminal}")

        b64 = payload.imageBase64.split(",")[-1]
        img_bytes = base64.b64decode(b64)

        report = run_grooming_analysis(b64)
        parsed = _parse_text_to_ui(report, payload.crewName, normalized_iga)

        img_path = upload_image_bytes(img_bytes, normalized_iga, "image", payload.crewName)

        # ⭐ Pass base and terminal to GCS save function
        upload_grooming_result_text(
            report,
            payload.crewName,
            normalized_iga,
            img_path,
            parsed=parsed,
            assessment_mode="image",  # Track as image assessment
            base=payload.base,  # ⭐ NEW
            terminal=payload.terminal  # ⭐ NEW
        )

        append_event_to_crew_log(
            {
                "type": "image",
                "parsed": parsed,
                "image_path": img_path,
                "base": payload.base,  # ⭐ NEW
                "terminal": payload.terminal  # ⭐ NEW
            },
            payload.crewName,
            normalized_iga,
        )

        create_ticket(
            {
                "type": "image",
                "igaCode": normalized_iga,
                "crewName": payload.crewName,
                "image_path": img_path,
                "base": payload.base,  # ⭐ NEW
                "terminal": payload.terminal  # ⭐ NEW
            }
        )

        print(f"✅ Photo assessment saved with base: {payload.base}")
        return {"status": "ok", "result": parsed}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

# ============= Grooming (video) =============

def _normalize_iga_code(iga_code: str) -> str:
    """Ensure IGA code has proper IGA prefix format."""
    if not iga_code:
        return iga_code
    
    iga_clean = iga_code.strip().upper()
    
    # If it's just numbers, add IGA prefix
    if iga_clean.isdigit():
        return f"IGA{iga_clean}"
    
    # If it already starts with IGA, return as-is
    if iga_clean.startswith("IGA"):
        return iga_clean
    
    # Otherwise, add IGA prefix
    return f"IGA{iga_clean}"

@app.post("/check-grooming-video")
async def check_grooming_video(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...),
    base: Optional[str] = Form(None),  # ⭐ NEW: Base location
    terminal: Optional[str] = Form(None),  # ⭐ NEW: Terminal
):
    """
    Video assessment with full persistence to GCS.
    Now includes base and terminal information.
    """
    MAX_SIZE = 20 * 1024 * 1024  # 20 MB
    size = 0
    chunk_size = 1024 * 1024  # 1 MB chunks

    await video.seek(0)
    while True:
        chunk = await video.read(chunk_size)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_SIZE:
            return JSONResponse({"error": "Video size must be <= 20 MB"}, status_code=400)

    await video.seek(0)

    try:
        # ⭐ Normalize IGA code to ensure proper format
        normalized_iga = _normalize_iga_code(iga_code)
        
        # ⭐ Log received data
        print(f"📥 Received video assessment:")
        print(f" IGA Code: {iga_code} → {normalized_iga}")
        print(f" Name: {name}")
        print(f" Base: {base}")
        print(f" Terminal: {terminal}")

        videos_dir = os.path.join("uploads", "videos")
        os.makedirs(videos_dir, exist_ok=True)

        video_path = os.path.join(videos_dir, video.filename)
        with open(video_path, "wb") as f:
            shutil.copyfileobj(video.file, f)

        full_text = check_grooming_from_video(video_path, name, normalized_iga)
        parsed = _parse_text_to_ui(full_text, name, normalized_iga)

        video_bytes = open(video_path, 'rb').read()
        video_gcs_path = upload_image_bytes(video_bytes, normalized_iga, "video", name)

        # ⭐ Pass base and terminal to GCS save function
        upload_grooming_result_text(
            full_text,
            name,
            normalized_iga,
            video_gcs_path,
            parsed=parsed,
            assessment_mode="video",  # Track as video assessment
            base=base,  # ⭐ NEW
            terminal=terminal  # ⭐ NEW
        )

        append_event_to_crew_log(
            {
                "type": "video",
                "parsed": parsed,
                "video_path": video_gcs_path,
                "base": base,  # ⭐ NEW
                "terminal": terminal  # ⭐ NEW
            },
            name,
            normalized_iga,
        )

        create_ticket(
            {
                "type": "video",
                "igaCode": normalized_iga,
                "crewName": name,
                "video_path": video_gcs_path,
                "base": base,  # ⭐ NEW
                "terminal": terminal  # ⭐ NEW
            }
        )

        print(f"✅ Video assessment saved with base: {base}")
        return {"status": "ok", "result": parsed}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

# ============= The 3 requested APIs =============

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
    pageSize: int = 20,  # ← CHANGED TO 20 (consistent with preset filters)
):
    """
    Get insights data with consistent pagination (20 records) and full date range trends.
    When using preset date ranges (days=7, days=14) or custom date filters,
    recent tests will always show 20 records (not 25).
    Trends data now includes ALL dates in the range, including dates with zero testing.
    """
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

# ============= ⭐ NEW: Base-wise Insights APIs =============

@app.get("/v1/insights/by-base")
async def insights_by_base(
    dateFrom: Optional[_date] = Query(None),
    dateTo: Optional[_date] = Query(None),
    days: Optional[int] = Query(None),
    topN: int = Query(5, description="Number of top bases to return")
):
    """
    Get compliance statistics grouped by base (summary only).
    Returns top N bases by total test count.
    """
    _from, _to = _resolve_range(dateFrom, dateTo, days)
    print(f"[BY-BASE] Fetching data from {_from} to {_to}")

    # Load all records
    filters = DashboardFilters(date_from=_from, date_to=_to)
    records = _load_records(filters)

    print(f"[BY-BASE] Loaded {len(records)} total records")

    # Group by base
    base_stats = defaultdict(lambda: {"compliant": 0, "nonCompliant": 0})

    for record in records:
        # ⭐ Read base from record (stored from frontend localStorage)
        base = record.get("base") or record.get("terminal") or "UNKNOWN"

        if record["assessment"] == "COMPLIANT":
            base_stats[base]["compliant"] += 1
        else:
            base_stats[base]["nonCompliant"] += 1

    # Convert to list and calculate totals
    result = []
    for base, stats in base_stats.items():
        total = stats["compliant"] + stats["nonCompliant"]
        result.append({
            "base": base,
            "compliant": stats["compliant"],
            "nonCompliant": stats["nonCompliant"],
            "total": total
        })

    # Sort by total tests descending, take top N
    result.sort(key=lambda x: x["total"], reverse=True)

    print(f"[BY-BASE] Returning {len(result[:topN])} bases")
    for base in result[:topN]:
        print(f"[BY-BASE] {base['base']}: {base['total']} tests ({base['compliant']} compliant)")

    return {
        "meta": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "filters": {
                "dateFrom": _from.isoformat(),
                "dateTo": _to.isoformat()
            }
        },
        "bases": result[:topN]
    }

@app.get("/v1/insights/by-base-with-crew")
async def insights_by_base_with_crew(
    dateFrom: Optional[_date] = Query(None),
    dateTo: Optional[_date] = Query(None),
    days: Optional[int] = Query(None),
    topN: int = Query(5, description="Number of top bases to return")
):
    """
    Get compliance statistics grouped by base with crew member breakdown.
    Returns top N bases by total test count with crew details.
    """
    _from, _to = _resolve_range(dateFrom, dateTo, days)
    print(f"[BY-BASE-CREW] Fetching data from {_from} to {_to}")

    # Load all records
    filters = DashboardFilters(date_from=_from, date_to=_to)
    records = _load_records(filters)

    print(f"[BY-BASE-CREW] Loaded {len(records)} total records")

    # Group by base, then by crew
    base_stats = defaultdict(lambda: {
        "compliant": 0,
        "nonCompliant": 0,
        "crew": defaultdict(lambda: {"compliant": 0, "nonCompliant": 0})
    })

    for record in records:
        # ⭐ Read base from record
        base = record.get("base") or record.get("terminal") or "UNKNOWN"
        iga_code = record.get("iga_code")
        crew_name = record.get("crew_name")

        # Base-level aggregation
        if record["assessment"] == "COMPLIANT":
            base_stats[base]["compliant"] += 1
            base_stats[base]["crew"][(iga_code, crew_name)]["compliant"] += 1
        else:
            base_stats[base]["nonCompliant"] += 1
            base_stats[base]["crew"][(iga_code, crew_name)]["nonCompliant"] += 1

    # Build result with crew details
    result = []
    for base, stats in base_stats.items():
        total = stats["compliant"] + stats["nonCompliant"]

        # Build crew list
        crew_list = []
        for (iga_code, crew_name), crew_stats in stats["crew"].items():
            crew_total = crew_stats["compliant"] + crew_stats["nonCompliant"]
            crew_list.append({
                "igaCode": iga_code,
                "crewName": crew_name,
                "compliant": crew_stats["compliant"],
                "nonCompliant": crew_stats["nonCompliant"],
                "total": crew_total,
                "passRate": round((crew_stats["compliant"] / crew_total * 100), 2) if crew_total > 0 else 0
            })

        # Sort crew by total tests descending
        crew_list.sort(key=lambda x: x["total"], reverse=True)

        result.append({
            "base": base,
            "compliant": stats["compliant"],
            "nonCompliant": stats["nonCompliant"],
            "total": total,
            "crewMembers": crew_list
        })

    # Sort by total tests descending, take top N
    result.sort(key=lambda x: x["total"], reverse=True)

    print(f"[BY-BASE-CREW] Returning {len(result[:topN])} bases with crew details")

    return {
        "meta": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "filters": {
                "dateFrom": _from.isoformat(),
                "dateTo": _to.isoformat()
            }
        },
        "bases": result[:topN]
    }

# ============= Individual Analysis - FINAL COMPLETE FIX =============

@app.get("/v1/individual-analysis")
async def individual_analysis(
    igaCode: str = Query(..., description="IGA code (e.g., IGA6781)"),
    crewName: str = Query(None, description="Crew name (e.g., Lavanya Singh) - Optional"),
    dateFrom: str = Query(..., description="Start date in YYYY-MM-DD format"),
    dateTo: str = Query(..., description="End date in YYYY-MM-DD format"),
):
    """
    Individual Analysis - Now with optional crewName!

    KEY PRINCIPLES:
    - Load ALL records from GCS for date range
    - Filter by crew (if crewName provided)
    - Count violations from ALL records (COMPLIANT or NON-COMPLIANT)
    - If a category has ANY issues in ANY assessment, increment by 1 (not by issue count)
    - Show violations as count and percentage

    UPDATED: crewName is now OPTIONAL
    - If crewName provided: Filter by both igaCode and crewName
    - If crewName is None/empty: Show data for igaCode across ALL crew members
    """
    try:
        # ============= PARSE & VALIDATE DATES =============
        try:
            start_date = datetime.strptime(dateFrom, "%Y-%m-%d").date()
            end_date = datetime.strptime(dateTo, "%Y-%m-%d").date()
        except ValueError as e:
            return JSONResponse(
                {
                    "error": "Invalid date format",
                    "details": f"Dates must be in YYYY-MM-DD format: {str(e)}"
                },
                status_code=400
            )

        if start_date > end_date:
            return JSONResponse(
                {
                    "error": "Invalid date range",
                    "details": "dateFrom cannot be after dateTo"
                },
                status_code=400
            )

        # ============= LOAD FRESH RECORDS FROM GCS =============
        print(f"\n[INDIVIDUAL-ANALYSIS] Starting fresh load from GCS")
        print(f"[INDIVIDUAL-ANALYSIS] Date range: {start_date} to {end_date}")
        print(f"[INDIVIDUAL-ANALYSIS] IGA Code: {igaCode}")
        print(f"[INDIVIDUAL-ANALYSIS] Crew Name: {crewName} (Optional)")

        filters = DashboardFilters(date_from=start_date, date_to=end_date)
        all_records = _load_records(filters)

        print(f"[INDIVIDUAL-ANALYSIS] Loaded {len(all_records)} total records from GCS")

        # ============= FILTER BY IGA CODE AND OPTIONAL CREW NAME =============
        iga_search = igaCode.strip().upper()
        crew_search = (crewName or "").strip().upper()  # Empty string if crewName is None

        crew_records = []
        for record in all_records:
            record_iga = (record.get("iga_code") or "").strip().upper()
            record_crew = (record.get("crew_name") or "").strip().upper()

            # If crewName provided, filter by both
            if crew_search:
                if record_iga == iga_search and record_crew == crew_search:
                    crew_records.append(record)
            else:
                # If crewName not provided, match only igaCode
                if record_iga == iga_search:
                    crew_records.append(record)

        print(f"[INDIVIDUAL-ANALYSIS] Filtered to {len(crew_records)} crew records")

        if not crew_records:
            print(f"[INDIVIDUAL-ANALYSIS] No records found for {iga_search} / {crew_search}")
            return JSONResponse(
                {
                    "error": "No assessments found for this crew in given date range",
                    "debug": {
                        "searchedFor": {
                            "igaCode": igaCode,
                            "crewName": crewName or "(not specified - all crew members)",
                            "dateRange": f"{dateFrom} to {dateTo}"
                        },
                        "totalRecordsInRange": len(all_records),
                        "matchingRecords": 0,
                    }
                },
                status_code=404
            )

        # ============= CALCULATE SUMMARY STATISTICS =============
        total = len(crew_records)
        compliant_count = sum(1 for r in crew_records if r["assessment"] == "COMPLIANT")
        noncompliant_count = total - compliant_count
        pass_rate = (compliant_count / total * 100) if total > 0 else 0

        print(f"[INDIVIDUAL-ANALYSIS] Summary: Total={total}, Compliant={compliant_count}, NC={noncompliant_count}, PassRate={pass_rate:.2f}%")

        # ============= CRITICAL: CATEGORY BREAKDOWN =============
        print(f"\n[INDIVIDUAL-ANALYSIS] === CATEGORY BREAKDOWN CALCULATION ===")

        # Count violations by category from ALL records (regardless of overall assessment status)
        category_violation_count = defaultdict(int)

        for record in crew_records:
            issues = record.get("issues") or []
            print(f"[INDIVIDUAL-ANALYSIS] Processing record: {record.get('timestamp')}, Assessment: {record['assessment']}")
            print(f"[INDIVIDUAL-ANALYSIS] Issues: {issues}")

            # ✅ FIX: Collect UNIQUE categories that have issues in THIS record
            cats_found = set()
            for issue in issues:
                heading = _issue_heading(issue)
                if heading in ["uniform", "hairstyle", "makeup", "nails", "accessories"]:
                    cats_found.add(heading)  # ← Add to set (no duplicates)
                    print(f"[INDIVIDUAL-ANALYSIS] Issue '{issue}' → Category '{heading}'")
                else:
                    print(f"[INDIVIDUAL-ANALYSIS] Issue '{issue}' → Skipped (category: '{heading}')")

            # ✅ FIX: Increment count by 1 for each UNIQUE category in this record
            for cat in cats_found:
                category_violation_count[cat] += 1
                print(f"[INDIVIDUAL-ANALYSIS] Category '{cat}' incremented to {category_violation_count[cat]}")

        # Convert to final format: violations + percentage
        category_breakdown = {}
        for cat in ["uniform", "hairstyle", "makeup", "nails", "accessories"]:
            violation_count = category_violation_count.get(cat, 0)
            percentage = (violation_count / total * 100) if total > 0 else 0
            category_breakdown[cat] = {
                "violations": violation_count,
                "percentage": round(percentage, 2)
            }
            print(f"[INDIVIDUAL-ANALYSIS] {cat.upper()}: violations={violation_count}, total={total}, percentage={percentage:.2f}%")

        print(f"[INDIVIDUAL-ANALYSIS] === END CATEGORY BREAKDOWN ===\n")

        # ============= BUILD DAILY TRENDS =============
        daily_stats = defaultdict(lambda: {"compliant": 0, "nonCompliant": 0})

        for record in crew_records:
            record_date = record["date"]
            if record["assessment"] == "COMPLIANT":
                daily_stats[record_date]["compliant"] += 1
            else:
                daily_stats[record_date]["nonCompliant"] += 1

        # Build trend array
        trend_list = []
        current_date = start_date
        while current_date <= end_date:
            date_key = current_date.isoformat()
            stats = daily_stats.get(current_date, {"compliant": 0, "nonCompliant": 0})
            trend_list.append({
                "date": date_key,
                "compliant": stats["compliant"],
                "nonCompliant": stats["nonCompliant"]
            })
            current_date += timedelta(days=1)

        # ============= BUILD RESPONSE =============
        response = {
            "crew": {
                "igaCode": igaCode,
                "name": crewName if crewName else "(All crew members with this IGA code)"
            },
            "dateRange": {
                "from": dateFrom,
                "to": dateTo,
                "daysAnalyzed": (end_date - start_date).days + 1
            },
            "summary": {
                "totalAssessments": total,
                "compliant": compliant_count,
                "nonCompliant": noncompliant_count,
                "passRate": f"{pass_rate:.2f}",
                "passRatePercentage": f"{pass_rate:.2f}%"
            },
            "nonComplianceByCategory": category_breakdown,
            "trend": trend_list,
            "recentAssessments": [
                {
                    "timestamp": r["timestamp"].isoformat() if r["timestamp"] else None,
                    "date": r["date"].isoformat(),
                    "score": r["score"],
                    "assessment": r["assessment"],
                    "issues": r["issues"]
                }
                for r in crew_records[:10]
            ]
        }

        print(f"[INDIVIDUAL-ANALYSIS] Returning response with {len(crew_records)} records\n")
        return response

    except Exception as e:
        import traceback
        print(f"[ERROR] individual_analysis: {str(e)}")
        print(traceback.format_exc())
        return JSONResponse(
            {
                "error": "Internal server error",
                "details": str(e),
                "type": type(e).__name__
            },
            status_code=500
        )

# ============= Entrypoint =============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=True)

