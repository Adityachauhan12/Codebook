"""
Dashboard service for grooming analytics and insights.

CRITICAL FIX v2: Consolidates BOTH new AND old category formats.

- NEW format: Already mapped to 5 categories
- OLD format: Granular categories like "uniform violation", "subject-standard mismatch"
  are NOW consolidated to 5 main categories

This ensures individual-analysis works with historical data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional
import re
from collections import Counter, defaultdict
import os

# threshold for score-based compliance fallback (default 7.0)
PASS_THRESHOLD = float(os.getenv("GROOMING_PASS_THRESHOLD", "7.0"))

# Reuse your GCS helpers
from gcs_utils import _list_by_prefix, _download_json, GCS_BASE_FOLDER

# ---------- Small helpers ----------

def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")

def _daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)

_rx = {
    "overall_assessment": re.compile(r"Overall\s*Assessment\s*:\s*(COMPLIANT|NON-?COMPLIANT)", re.I),
    "overall_score": re.compile(r"Overall\s*Score\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10", re.I),
    "issues_block": re.compile(r"Issues\s*Found\s*:\s*(.+?)(?:\n\s*\n|$)", re.I | re.S),
}

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

def _parse_result_text(text: str) -> Dict[str, Any]:
    """
    Extract assessment, score, and issues list from Gemini result_text.
    Robust to formatting variations; falls back to score-based rule if assessment missing.
    """
    raw = text or ""
    clean = raw.replace("**", "").strip()

    # Accept 'Overall Assessment :', 'Overall Assessment -', '–', case-insensitive,
    # allow 'NON COMPLIANT' / 'NON-COMPLIANT'
    patt = re.compile(
        r"overall\s*assessment\s*[:\-–]\s*(compliant|non[\s\-]*compliant)",
        re.I
    )
    m_assess = patt.search(clean)

    # Score (e.g., 'Overall Score: 7/10')
    m_score = re.search(
        r"overall\s*score\s*[:\-–]\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10",
        clean, re.I
    )

    # Issues block (bullets)
    m_issues = _rx["issues_block"].search(clean)

    # Normalize assessment
    assessment = None
    if m_assess:
        a = m_assess.group(1).strip().upper()
        a = a.replace("NON COMPLIANT", "NON-COMPLIANT").replace("NONCOMPLIANT", "NON-COMPLIANT")
        assessment = "COMPLIANT" if a == "COMPLIANT" else "NON-COMPLIANT"

    # Parse score to float
    score = None
    if m_score:
        try:
            score = float(m_score.group(1))
        except Exception:
            score = None

    # Fallback: if assessment missing, infer from score threshold
    if not assessment:
        if isinstance(score, (int, float)):
            assessment = "COMPLIANT" if score >= PASS_THRESHOLD else "NON-COMPLIANT"
        else:
            # As a safe default (no score + no assessment), treat as NON-COMPLIANT
            assessment = "NON-COMPLIANT"

    # Parse issues (bulleted lines)
    issues: List[str] = _lines_to_list(m_issues.group(1)) if m_issues else []

    return {"assessment": assessment, "score": score, "issues": issues}

# =========================================================================
# CRITICAL FIX v2: Enhanced _issue_heading() - NOW CONSOLIDATES OLD FORMATS TOO
# =========================================================================

def _issue_heading(s: str) -> str:
    """
    Map ANY grooming issue to ONE of the 5 MAIN categories:
    - Uniform
    - Hairstyle
    - Makeup
    - Nails
    - Accessories

    FIXED: Now consolidates BOTH new AND old category formats.
    Old granular categories are now properly mapped.
    """
    s = (s or "").strip().lower()

    # Remove common prefixes/noise
    s = s.replace(",", "").replace(":", "")

    # ===== UNIFORM =====
    # Maps: uniform, tunic, scarf, badge, stockings, AND OLD granular categories
    uniform_keywords = [
        "uniform", "tunic", "scarf", "badge", "name badge",
        "stockings", "attire", "dress", "clothing",
        "subject-standard mismatch",  # OLD: Maps to Uniform
        "items not visible",  # OLD: Maps to Uniform
        "incomplete view",  # OLD: Maps to Uniform
        "image quality",  # OLD: Maps to Uniform
        "uniform violation",  # OLD: Maps to Uniform
        "total non-compliance",  # OLD: Maps to Uniform
        "**uniform**",  # NEW: Handle markdown formatting
        "indigo uniform",  # NEW: Specific uniform mentions
        "approved uniform"  # NEW: Uniform compliance
    ]
    for kw in uniform_keywords:
        if kw in s:
            return "uniform"

    # ===== HAIRSTYLE =====
    hairstyle_keywords = [
        "hair", "hairstyle", "hair style", "bun", "braid",
        "ponytail", "chignon", "curl", "hair color", "highlights",
        "beard", "mustache", "moustache", "facial hair",  # Include facial hair
        "grooming non-compliance",  # Sometimes refers to hairstyle
        "**hairstyle**",  # NEW: Handle markdown formatting
        "bob cut", "bob", "approved style",  # NEW: Specific hairstyle mentions
        "secured style", "hair worn down"  # NEW: Hair styling issues
    ]
    for kw in hairstyle_keywords:
        if kw in s:
            return "hairstyle"

    # ===== ACCESSORIES =====
    # Includes: earrings, rings, watch, bangles, religious items, nose pins
    accessories_keywords = [
        "accessor", "earring", "ring", "watch", "bangle",
        "bracelet", "jewelry", "jewellery", "stud",
        "nose pin", "piercing", "religious thread",
        "prohibited accessor",  # Catch prohibited items
        "earbud",  # Sometimes classified as accessory
        "prohibited accessories",  # NEW: OLD format support
        "**accessories**",  # NEW: Handle markdown formatting
        "necklace", "mandatory watch", "seconds hand",  # NEW: Specific accessories
        "non-standard necklace"  # NEW: Accessory violations
    ]
    for kw in accessories_keywords:
        if kw in s:
            return "accessories"

    # ===== MAKEUP =====
    makeup_keywords = [
        "makeup", "make-up", "make up", "foundation", "base",
        "eyeshadow", "eye shadow", "liner", "eyeliner",
        "mascara", "lipstick", "lip", "cosmetic", "lip color",
        "**makeup**",  # NEW: Handle markdown formatting
        "non-compliant", "shades"  # NEW: When combined with makeup context
    ]
    for kw in makeup_keywords:
        if kw in s:
            return "makeup"

    # ===== NAILS =====
    nails_keywords = [
        "nail", "manicure", "nail polish", "nail color",
        "**nails**"  # NEW: Handle markdown formatting
    ]
    for kw in nails_keywords:
        if kw in s:
            return "nails"

    # ===== SPECIAL CASES =====
    # Handle generic assessment issues - map to uniform as default category
    generic_keywords = [
        "assessment criteria", "gender-specific", "male crew member",
        "female standards", "does not comply", "criteria are"
    ]
    for kw in generic_keywords:
        if kw in s:
            return "uniform"  # Map generic issues to uniform category

    # Default fallback
    return "uniform"  # Changed from "other" to "uniform" to ensure categorization

@dataclass
class Filters:
    date_from: date
    date_to: date

# ---------- Read records from GCS ----------

def _load_records(filters: Filters) -> List[Dict[str, Any]]:
    """
    Read grooming result records from GCS and build normalized rows.
    """
    records: List[Dict[str, Any]] = []
    print(f"\n🔍 Loading records from {filters.date_from} to {filters.date_to}")

    for d in _daterange(filters.date_from, filters.date_to):
        date_prefix = f"{GCS_BASE_FOLDER}/{_yyyymmdd(d)}/results/"
        print(f"📁 Checking date prefix: {date_prefix}")

        # Get all blobs under the date/results/ path (includes IGA subfolders)
        all_blobs = _list_by_prefix(date_prefix)
        json_blobs = [blob for blob in all_blobs if blob.name.endswith(".json")]

        print(f"📊 Found {len(json_blobs)} JSON files for {d}")

        for blob in json_blobs:
            # Load one result JSON
            try:
                doc = _download_json(blob.name)
            except Exception:
                # If this one fails to load, skip safely
                continue

            # Handle both old and new data formats
            # New format has direct fields, old format may have parsed blob or raw text

            # Try direct fields first (new format)
            assessment = (doc.get("assessment") or "").strip().upper()
            score = doc.get("score")
            issues = doc.get("issues") or []

            # If no direct assessment, try parsed blob
            if not assessment or assessment not in ("COMPLIANT", "NON-COMPLIANT"):
                parsed_blob = doc.get("parsed") if isinstance(doc.get("parsed"), dict) else {}
                if parsed_blob:
                    assessment = (parsed_blob.get("assessment") or "").strip().upper()
                    score = parsed_blob.get("score") or score
                    issues = parsed_blob.get("issues") or issues

            # Final fallback: parse raw text
            if not assessment or assessment not in ("COMPLIANT", "NON-COMPLIANT"):
                p = _parse_result_text(doc.get("raw_text") or doc.get("result_text", ""))
                assessment = p["assessment"]
                score = p["score"] if score is None else score
                issues = p["issues"] if not issues else issues

            # Normalize assessment with PASS_THRESHOLD-based fallback if missing/invalid
            if assessment not in ("COMPLIANT", "NON-COMPLIANT"):
                if isinstance(score, (int, float)):
                    assessment = "COMPLIANT" if score >= PASS_THRESHOLD else "NON-COMPLIANT"
                else:
                    assessment = "NON-COMPLIANT"
            
            # Fix misclassified assessments with real grooming issues
            if assessment == "COMPLIANT" and issues:
                real_issues = [i for i in issues if not any(p in i.lower() for p in ["not visible", "cannot be assessed"])]
                if real_issues and isinstance(score, (int, float)) and score < 7:
                    assessment = "NON-COMPLIANT"

            # Parse timestamp safely and make timezone-aware
            ts_str = doc.get("timestamp")
            try:
                if ts_str:
                    if ts_str.endswith('Z'):
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    else:
                        ts = datetime.fromisoformat(ts_str)
                    # Make timezone-aware if naive
                    if ts.tzinfo is None:
                        from datetime import timezone
                        ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = None
            except Exception:
                ts = None

            # Extract assessment mode from document
            assessment_mode = doc.get("assessment_mode", "image")  # default to image for backward compatibility

            # Build normalized record
            record = {
                "timestamp": ts,
                "date": (ts.date() if ts else d),
                "iga_code": (doc.get("iga_code") or "").strip(),
                "crew_name": (doc.get("crew_name") or "").strip(),
                "score": score if isinstance(score, (int, float)) else score,
                "assessment": assessment,
                "issues": issues or [],
                "assessment_mode": assessment_mode,  # track whether it was video or image assessment
                # ⭐ NEW: Read base and terminal from saved records
                "base": doc.get("base") or "UNKNOWN",
                "terminal": doc.get("terminal") or "UNKNOWN",
                "department": doc.get("department"),
            }

            records.append(record)
            print(f"✅ Loaded record: {record['iga_code']} - {record['assessment']} - {len(record['issues'])} issues")
            if record['issues']:
                print(f"   Issues: {record['issues'][:3]}{'...' if len(record['issues']) > 3 else ''}")

    # Most recent first - use timezone-aware datetime.min
    from datetime import timezone
    min_datetime = datetime.min.replace(tzinfo=timezone.utc)

    records.sort(key=lambda r: (r["timestamp"] or min_datetime), reverse=True)

    print(f"✅ Total records loaded: {len(records)}")
    
    # Debug: Show sample of issues from loaded records
    nc_records_with_issues = [r for r in records if r['assessment'] == 'NON-COMPLIANT' and r['issues']]
    print(f"🔍 Found {len(nc_records_with_issues)} NON-COMPLIANT records with issues")
    for i, r in enumerate(nc_records_with_issues[:3]):  # Show first 3
        print(f"   Sample {i+1}: {r['iga_code']} has {len(r['issues'])} issues: {r['issues'][:2]}")
    
    return records

# ---------- Core aggregations ----------

def _looks_like_iga(iga: str) -> bool:
    if not iga:
        return False
    iga = iga.strip().upper()
    # adjust if your real pattern differs:
    return bool(re.fullmatch(r"IGA\d{4,6}", iga))

def _overview(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    comp = sum(1 for r in records if r["assessment"] == "COMPLIANT")
    nonc = total - comp

    return {
        "total": total,
        "compliant": comp,
        "nonCompliant": nonc,
        "passRate": (comp / total if total else 0.0),
        "failRate": (nonc / total if total else 0.0),
        "base": None  # explicitly include base
    }

def _daily_graph(records: List[Dict[str, Any]], start_date: date = None, end_date: date = None) -> List[Dict[str, Any]]:
    """
    Generate daily graph data for ALL dates in range (including zero-testing dates).
    Returns compliant=0, nonCompliant=0 for dates with no records.

    Args:
        records: List of assessment records
        start_date: Start of date range (optional - if None, only return dates with data)
        end_date: End of date range (optional - if None, only return dates with data)
    """
    daily_compliant: Dict[date, int] = defaultdict(int)
    daily_noncomp: Dict[date, int] = defaultdict(int)

    # Build counts from records
    for r in records:
        d = r["date"]
        if r["assessment"] == "COMPLIANT":
            daily_compliant[d] += 1
        else:
            daily_noncomp[d] += 1

    # Determine date range to return
    if start_date is None or end_date is None:
        # Fallback: only days with data
        all_days = sorted(set(list(daily_compliant.keys()) + list(daily_noncomp.keys())))
    else:
        # NEW: Include ALL dates in range (including zero-testing dates)
        # This ensures graph data matches UI visualization
        all_days = [d for d in _daterange(start_date, end_date)]

    return [{
        "date": d.isoformat(),
        "compliant": daily_compliant.get(d, 0),
        "nonCompliant": daily_noncomp.get(d, 0),
        "base": None
    } for d in all_days]

def _category_breakdown(records: List[Dict[str, Any]], top_n: int = 10) -> List[Dict[str, Any]]:
    VALID_CATEGORIES = {"uniform", "hairstyle", "makeup", "nails", "accessories"}
    SKIP_PHRASES = ["gender-specific", "male crew member", "female standards", "assessment criteria", 
                   "light not adequate", "lighting", "image quality", "not visible", "cannot be assessed", 
                   "could not be assessed", "incomplete view", "visibility", "camera", "angle"]
    
    headings = []
    for r in records:
        if r["assessment"] != "NON-COMPLIANT":
            continue
            
        # Filter to actual grooming defects only
        grooming_issues = [issue for issue in r.get("issues", []) 
                          if not any(phrase in issue.lower() for phrase in SKIP_PHRASES)]
        
        if not grooming_issues:
            continue
            
        # Get unique categories for this record
        categories = {_issue_heading(issue) for issue in grooming_issues}
        categories = {cat for cat in categories if cat in VALID_CATEGORIES}
        
        headings.extend(categories)
    
    counts = Counter(headings)
    return [{"category": cat, "nonCompliantCount": counts[cat], 
             "share": round(counts[cat] / len([r for r in records if r["assessment"] == "NON-COMPLIANT"]), 3) if counts[cat] else 0.0, 
             "base": None} 
            for cat in sorted(counts.keys(), key=lambda c: counts[c], reverse=True)][:top_n]

def _top_non_groomed(records: List[Dict[str, Any]], min_tests: int = 3, top_n: int = 5):
    per: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "crewName": "", "total": 0, "nonCompliant": 0, "sumScore": 0.0, "scored": 0, "lastSeen": None
    })

    for r in records:
        iga = (r["iga_code"] or "").strip()
        if not _looks_like_iga(iga):
            continue

        x = per[iga]
        x["crewName"] = r["crew_name"] or x["crewName"]
        x["total"] += 1

        if r["assessment"] == "NON-COMPLIANT":
            x["nonCompliant"] += 1

        if isinstance(r["score"], (int, float)):
            x["sumScore"] += r["score"]
            x["scored"] += 1

        ts = r.get("timestamp")
        if ts and (x["lastSeen"] is None or ts > x["lastSeen"]):
            x["lastSeen"] = ts

    rows = []
    for iga, x in per.items():
        if x["total"] < min_tests:
            continue

        avg_score = round(x["sumScore"]/x["scored"], 2) if x["scored"] else 0
        
        rows.append({
            "crewId": iga,
            "crewName": x["crewName"],
            "nonCompliant": x["nonCompliant"],
            "totalTests": x["total"],
            "nonCompliantRate": round(x["nonCompliant"]/x["total"], 3),
            "avgScore": avg_score,
            "lastSeen": x["lastSeen"].isoformat() if x["lastSeen"] else None,
            "base": None
        })

    # Sort by average score (lowest first) - worst performers at top
    rows.sort(key=lambda r: r["avgScore"])
    return rows[:top_n]

def _top_groomed(records: List[Dict[str, Any]], min_tests: int = 3, top_n: int = 5):
    per: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "crewName": "", "total": 0, "compliant": 0, "sumScore": 0.0, "scored": 0, "lastSeen": None
    })

    for r in records:
        iga = (r["iga_code"] or "").strip()
        if not _looks_like_iga(iga):
            continue

        x = per[iga]
        x["crewName"] = r["crew_name"] or x["crewName"]
        x["total"] += 1

        if r["assessment"] == "COMPLIANT":
            x["compliant"] += 1

        if isinstance(r["score"], (int, float)):
            x["sumScore"] += r["score"]
            x["scored"] += 1

        ts = r.get("timestamp")
        if ts and (x["lastSeen"] is None or ts > x["lastSeen"]):
            x["lastSeen"] = ts

    rows = []
    for iga, x in per.items():
        if x["total"] < min_tests:
            continue

        avg_score = round(x["sumScore"]/x["scored"], 2) if x["scored"] else 0
        
        rows.append({
            "crewId": iga,
            "crewName": x["crewName"],
            "compliant": x["compliant"],
            "totalTests": x["total"],
            "compliantRate": round(x["compliant"]/x["total"], 3),
            "avgScore": avg_score,
            "lastSeen": x["lastSeen"].isoformat() if x["lastSeen"] else None,
            "base": None
        })

    # Sort by average score (highest first) - Lavanya with lowest score will be at bottom
    rows.sort(key=lambda r: r["avgScore"], reverse=True)
    return rows[:top_n]

# ========== shared helpers ==========

def _safe_page_size(ps: int, default: int = 25, maximum: int = 200) -> int:
    try:
        n = int(ps)
        if n <= 0:
            return default
        return min(n, maximum)
    except Exception:
        return default

def _compute_recent_tests(records: List[Dict[str, Any]], page: int, page_size: int):
    start = max(0, (page - 1) * page_size)
    end = start + page_size

    items = []
    for r in records[start:end]:
        ts = r.get("timestamp")
        score = r.get("score")
        assessment = (r.get("assessment") or "").strip().upper()

        # Ensure assessment present (fallback by score)
        if assessment not in ("COMPLIANT", "NON-COMPLIANT"):
            if isinstance(score, (int, float)):
                assessment = "COMPLIANT" if score >= PASS_THRESHOLD else "NON-COMPLIANT"
            else:
                assessment = "NON-COMPLIANT"

        pass_fail = "PASS" if assessment == "COMPLIANT" else "FAIL"

        items.append({
            "testId": f"T-{int(ts.timestamp()) if ts else 0}",
            "crewId": r.get("iga_code"),
            "crewName": r.get("crew_name"),
            "base": r.get("base"),  # remains null until you start saving it
            "score": score if isinstance(score, (int, float)) else 0,
            "assessment": assessment,  # always COMPLIANT / NON-COMPLIANT
            "status": assessment,  # same as assessment for clarity
            "passFail": pass_fail,  # optional compatibility field
            "takenAt": ts.isoformat() if ts else None,
        })

    return {"items": items, "page": page, "pageSize": page_size, "total": len(records)}

# ============= PUBLIC FUNCTIONS (APIs) =============

def get_insights(date_from: date, date_to: date, page: int, page_size: int) -> Dict[str, Any]:
    """
    MAIN API: grooming vs non-grooming, categories, daily graph (compliant/nonCompliant),
    top 5 groomed & non-groomed, and recent tests. Includes base=null fields.

    FIXED:
    - Recent tests now always shows 20 records (consistent with preset filters)
    - Daily graph includes ALL dates in range (even zero-testing dates)
    - Data now matches graph UI visualization
    """
    records = _load_records(Filters(date_from=date_from, date_to=date_to))

    # FIXED: Always use 20 for recent tests (consistent across all filter types)
    # This ensures 1-week, 2-week, and custom date filters all show same 20 records
    effective_page_size = min(_safe_page_size(page_size, default=20, maximum=20), 20)

    meta = {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "filters": {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
    }

    return {
        "meta": meta,
        "overview": _overview(records),
        "trends": {"daily": _daily_graph(records, start_date=date_from, end_date=date_to)},  # PASS date range
        "categories": _category_breakdown(records),
        "top": {
            "groomed": _top_groomed(records, min_tests=3, top_n=5),
            "nonGroomed": _top_non_groomed(records, min_tests=3, top_n=5)
        },
        "recentTests": _compute_recent_tests(records, page, page_size=effective_page_size),  # USE 20
    }

def get_info(date_from: date, date_to: date) -> Dict[str, Any]:
    """
    GENERAL INFO BOX: cards-like basic info with base=null.
    """
    records = _load_records(Filters(date_from=date_from, date_to=date_to))
    k = _overview(records)

    avg_vals = [r["score"] for r in records if isinstance(r["score"], (int, float))]

    return {
        "meta": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "filters": {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()}
        },
        "info": {
            "totalTests": k["total"],
            "passRate": k["passRate"],
            "failRate": k["failRate"],
            "avgScore": round(sum(avg_vals)/len(avg_vals), 2) if avg_vals else 0.0,
            "uniqueCrewTested": len({(r["iga_code"], r["crew_name"]) for r in records}),
            "repeatOffenders": sum(1 for _, v in Counter([r["iga_code"] for r in records if r["assessment"] == "NON-COMPLIANT"]).items() if v >= 2),
            "base": None
        }
    }

def search_people(date_from: date, date_to: date, query: str, page: int, page_size: int) -> Dict[str, Any]:
    """
    SEARCH by IGA code or name (case-insensitive), aggregated per person.
    """
    records = _load_records(Filters(date_from=date_from, date_to=date_to))
    q = (query or "").strip().lower()

    per: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "crewName": "", "total": 0, "compliant": 0, "nonCompliant": 0,
        "sumScore": 0.0, "scored": 0, "lastSeen": None
    })

    for r in records:
        iga = (r["iga_code"] or "")
        name = (r["crew_name"] or "")

        if q and (q not in iga.lower()) and (q not in name.lower()):
            continue

        x = per[iga]
        x["crewName"] = name or x["crewName"]
        x["total"] += 1

        if r["assessment"] == "COMPLIANT":
            x["compliant"] += 1
        else:
            x["nonCompliant"] += 1

        if isinstance(r["score"], (int, float)):
            x["sumScore"] += r["score"]
            x["scored"] += 1

        ts = r.get("timestamp")
        if ts and (x["lastSeen"] is None or ts > x["lastSeen"]):
            x["lastSeen"] = ts

    rows = []
    for iga, x in per.items():
        rows.append({
            "crewId": iga,
            "crewName": x["crewName"],
            "totalTests": x["total"],
            "compliant": x["compliant"],
            "nonCompliant": x["nonCompliant"],
            "passRate": round(x["compliant"]/x["total"], 3) if x["total"] else 0.0,
            "avgScore": round(x["sumScore"]/x["scored"], 2) if x["scored"] else None,
            "lastSeen": x["lastSeen"].isoformat() if x["lastSeen"] else None,
            "base": None
        })

    rows.sort(key=lambda r: (r["lastSeen"] or "", r["totalTests"]), reverse=True)

    total = len(rows)
    start = max(0, (page - 1) * _safe_page_size(page_size))
    end = start + _safe_page_size(page_size)

    return {
        "query": query,
        "page": page,
        "pageSize": _safe_page_size(page_size),
        "total": total,
        "results": rows[start:end]
    }

def get_individual_analysis_data(iga_code: str, crew_name: Optional[str], date_from: date, date_to: date) -> Dict[str, Any]:
    """
    Get individual grooming analysis data for the grooming page including video assessments.
    """
    records = _load_records(Filters(date_from=date_from, date_to=date_to))
    
    # Filter records for the specific crew member
    crew_records = [r for r in records if (r["iga_code"] or "").strip().upper() == iga_code.strip().upper()]
    
    # Calculate summary statistics
    total_assessments = len(crew_records)
    compliant_count = sum(1 for r in crew_records if r["assessment"] == "COMPLIANT")
    non_compliant_count = total_assessments - compliant_count
    
    # Calculate pass rate
    pass_rate = round((compliant_count / total_assessments) * 100, 1) if total_assessments > 0 else 0.0
    
    # Count assessment modes
    video_count = sum(1 for r in crew_records if r.get("assessment_mode") == "video")
    image_count = sum(1 for r in crew_records if r.get("assessment_mode") == "image" or not r.get("assessment_mode"))
    
    # Calculate non-compliance by category
    non_compliant_records = [r for r in crew_records if r["assessment"] == "NON-COMPLIANT"]
    category_violations = defaultdict(int)
    
    for record in non_compliant_records:
        for issue in record.get("issues", []):
            # Skip "not visible" issues for nails category
            if "nail" in issue.lower() and any(phrase in issue.lower() for phrase in ["not visible", "cannot be assessed", "could not be assessed"]):
                continue
            
            category = _issue_heading(issue)
            if category and category != "other":
                category_violations[category] += 1
    
    # Calculate percentages for categories
    non_compliance_by_category = {}
    for category, violations in category_violations.items():
        percentage = (violations / len(non_compliant_records)) * 100 if non_compliant_records else 0
        non_compliance_by_category[category] = {
            "violations": violations,
            "percentage": round(percentage, 1)
        }
    
    # Generate trend data (daily breakdown)
    daily_data = defaultdict(lambda: {"compliant": 0, "nonCompliant": 0})
    for record in crew_records:
        record_date = record["date"].isoformat() if record["date"] else None
        if record_date:
            if record["assessment"] == "COMPLIANT":
                daily_data[record_date]["compliant"] += 1
            else:
                daily_data[record_date]["nonCompliant"] += 1
    
    trend = []
    for date_str in sorted(daily_data.keys()):
        trend.append({
            "date": date_str,
            "compliant": daily_data[date_str]["compliant"],
            "nonCompliant": daily_data[date_str]["nonCompliant"]
        })
    
    return {
        "summary": {
            "totalAssessments": total_assessments,
            "compliant": compliant_count,
            "nonCompliant": non_compliant_count,
            "passRate": f"{pass_rate}%",
            "passRatePercentage": f"{pass_rate}%",
            "recentAssessments": total_assessments,
            "videoAssessments": video_count,
            "imageAssessments": image_count
        },
        "nonComplianceByCategory": non_compliance_by_category,
        "trend": trend,
        "meta": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "filters": {
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
                "igaCode": iga_code,
                "crewName": crew_name
            }
        }
    }
