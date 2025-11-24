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
        "subject-standard mismatch",      # OLD: Maps to Uniform
        "items not visible",              # OLD: Maps to Uniform
        "incomplete view",                # OLD: Maps to Uniform
        "image quality",                  # OLD: Maps to Uniform
        "uniform violation",              # OLD: Maps to Uniform
        "total non-compliance"            # OLD: Maps to Uniform
    ]
    for kw in uniform_keywords:
        if kw in s:
            return "uniform"
    
    # ===== HAIRSTYLE =====
    hairstyle_keywords = [
        "hair", "hairstyle", "hair style", "bun", "braid", 
        "ponytail", "chignon", "curl", "hair color", "highlights",
        "beard", "mustache", "moustache", "facial hair",  # Include facial hair
        "grooming non-compliance"  # Sometimes refers to hairstyle
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
        "prohibited accessories"  # NEW: OLD format support
    ]
    for kw in accessories_keywords:
        if kw in s:
            return "accessories"
    
    # ===== MAKEUP =====
    makeup_keywords = [
        "makeup", "make-up", "make up", "foundation", "base",
        "eyeshadow", "eye shadow", "liner", "eyeliner", 
        "mascara", "lipstick", "lip", "cosmetic", "lip color"
    ]
    for kw in makeup_keywords:
        if kw in s:
            return "makeup"
    
    # ===== NAILS =====
    nails_keywords = [
        "nail", "manicure", "nail polish", "nail color"
    ]
    for kw in nails_keywords:
        if kw in s:
            return "nails"
    
    # Default fallback
    return "other"


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

    for d in _daterange(filters.date_from, filters.date_to):
        prefix = f"{GCS_BASE_FOLDER}/{_yyyymmdd(d)}/results/"
        
        # ✅ FIX: _list_by_prefix returns Blob objects
        blobs = _list_by_prefix(prefix)
        
        for blob in blobs:
            # ✅ FIX: Use blob.name (string) instead of blob (Blob object)
            if not blob.name.endswith(".json"):
                continue

            # Load one result JSON
            try:
                # ✅ FIX: Pass blob.name (string) to _download_json
                doc = _download_json(blob.name)
            except Exception:
                # If this one fails to load, skip safely
                continue

            # Prefer parsed blob if present
            parsed_blob = doc.get("parsed") if isinstance(doc.get("parsed"), dict) else {}

            # Top-level duplicates (present in new payloads)
            top_assessment = (doc.get("assessment") or "").strip().upper()
            top_score = doc.get("score")
            top_issues = doc.get("issues") or []

            # Pick source of truth in priority order
            if parsed_blob:
                assessment = (parsed_blob.get("assessment") or top_assessment or "").strip().upper()
                score = parsed_blob.get("score", top_score)
                issues = parsed_blob.get("issues") or top_issues
            else:
                # If top-level duplicates are valid, use them
                if top_assessment in ("COMPLIANT", "NON-COMPLIANT"):
                    assessment = top_assessment
                    score = top_score
                    issues = top_issues
                else:
                    # Legacy: parse raw Gemini result_text
                    p = _parse_result_text(doc.get("result_text", ""))
                    assessment, score, issues = p["assessment"], p["score"], p["issues"]

            # Normalize assessment with PASS_THRESHOLD-based fallback if missing/invalid
            if assessment not in ("COMPLIANT", "NON-COMPLIANT"):
                if isinstance(score, (int, float)):
                    assessment = "COMPLIANT" if score >= PASS_THRESHOLD else "NON-COMPLIANT"
                else:
                    assessment = "NON-COMPLIANT"

            # Parse timestamp safely
            ts_str = doc.get("timestamp")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
            except Exception:
                ts = None

            # Build normalized record
            records.append({
                "timestamp": ts,
                "date": (ts.date() if ts else d),
                "iga_code": (doc.get("iga_code") or "").strip(),
                "crew_name": (doc.get("crew_name") or "").strip(),
                "score": score if isinstance(score, (int, float)) else score,
                "assessment": assessment,
                "issues": issues or [],
                # ⭐ NEW: Read base and terminal from saved records
                "base": doc.get("base") or "UNKNOWN",
                "terminal": doc.get("terminal") or "UNKNOWN",
                "department": doc.get("department"),
            })

    # Most recent first
    records.sort(key=lambda r: (r["timestamp"] or datetime.min), reverse=True)
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


def _daily_graph(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    daily_compliant: Dict[date, int] = defaultdict(int)
    daily_noncomp: Dict[date, int] = defaultdict(int)
    for r in records:
        d = r["date"]
        if r["assessment"] == "COMPLIANT":
            daily_compliant[d] += 1
        else:
            daily_noncomp[d] += 1

    all_days = sorted(set(list(daily_compliant.keys()) + list(daily_noncomp.keys())))
    return [{
        "date": d.isoformat(),
        "compliant": daily_compliant.get(d, 0),
        "nonCompliant": daily_noncomp.get(d, 0),
        "base": None
    } for d in all_days]


def _category_breakdown(records: List[Dict[str, Any]], top_n: int = 10) -> List[Dict[str, Any]]:
    """
    CRITICAL FIX v2: Returns ONLY 5 MAIN CATEGORIES.
    Maps ALL issues (both new and old formats) to 5 categories.
    """
    VALID_CATEGORIES = {"uniform", "hairstyle", "makeup", "nails", "accessories"}
    
    headings: List[str] = []
    for r in records:
        if r["assessment"] == "NON-COMPLIANT":
            for raw in r.get("issues", []):
                s = raw
                head = _issue_heading(s)  # ← Uses FIXED function that handles old formats
                
                # Only count valid categories
                if head in VALID_CATEGORIES:
                    headings.append(head)
    
    total_nc = sum(1 for r in records if r["assessment"] == "NON-COMPLIANT")
    counts = Counter(headings)
    
    # Return ALL 5 categories sorted by count
    result = []
    for cat in sorted(counts.keys(), key=lambda c: counts[c], reverse=True):
        result.append({
            "category": cat,
            "nonCompliantCount": counts[cat],
            "share": round((counts[cat] / total_nc), 3) if total_nc else 0.0,
            "base": None
        })
    
    return result[:top_n]


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
            x["sumScore"] += r["score"]; x["scored"] += 1
        ts = r.get("timestamp")
        if ts and (x["lastSeen"] is None or ts > x["lastSeen"]):
            x["lastSeen"] = ts
    rows = []
    for iga, x in per.items():
        if x["total"] < min_tests or x["nonCompliant"] == 0:
            continue
        rows.append({
            "crewId": iga,
            "crewName": x["crewName"],
            "nonCompliant": x["nonCompliant"],
            "totalTests": x["total"],
            "nonCompliantRate": round(x["nonCompliant"]/x["total"], 3),
            "avgScore": round(x["sumScore"]/x["scored"], 2) if x["scored"] else None,
            "lastSeen": x["lastSeen"].isoformat() if x["lastSeen"] else None,
            "base": None
        })
    rows.sort(key=lambda r: (r["nonCompliant"], r["nonCompliantRate"], r["totalTests"], r["lastSeen"] or datetime.min), reverse=True)
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
            x["sumScore"] += r["score"]; x["scored"] += 1
        ts = r.get("timestamp")
        if ts and (x["lastSeen"] is None or ts > x["lastSeen"]):
            x["lastSeen"] = ts
    rows = []
    for iga, x in per.items():
        if x["total"] < min_tests or x["compliant"] == 0:
            continue
        rows.append({
            "crewId": iga,
            "crewName": x["crewName"],
            "compliant": x["compliant"],
            "totalTests": x["total"],
            "compliantRate": round(x["compliant"]/x["total"], 3),
            "avgScore": round(x["sumScore"]/x["scored"], 2) if x["scored"] else None,
            "lastSeen": x["lastSeen"].isoformat() if x["lastSeen"] else None,
            "base": None
        })
    rows.sort(key=lambda r: (r["compliant"], r["compliantRate"], r["totalTests"], r["lastSeen"] or datetime.min), reverse=True)
    return rows[:top_n]


# ========== shared helpers ==========
def _safe_page_size(ps: int, default: int = 25, maximum: int = 200) -> int:
    try:
        n = int(ps)
        if n <= 0: return default
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
            "base": r.get("base"),              # remains null until you start saving it
            "score": score if isinstance(score, (int, float)) else 0,
            "assessment": assessment,           # always COMPLIANT / NON-COMPLIANT
            "status": assessment,               # same as assessment for clarity
            "passFail": pass_fail,              # optional compatibility field
            "takenAt": ts.isoformat() if ts else None,
        })
    return {"items": items, "page": page, "pageSize": page_size, "total": len(records)}


# ============= PUBLIC FUNCTIONS (APIs) =============

def get_insights(date_from: date, date_to: date, page: int, page_size: int) -> Dict[str, Any]:
    """
    MAIN API: grooming vs non-grooming, categories, daily graph (compliant/nonCompliant),
    top 5 groomed & non-groomed, and recent tests. Includes base=null fields.
    """
    records = _load_records(Filters(date_from=date_from, date_to=date_to))
    meta = {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "filters": {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
    }
    return {
        "meta": meta,
        "overview": _overview(records),
        "trends": {"daily": _daily_graph(records)},
        "categories": _category_breakdown(records),
        "top": {
            "groomed": _top_groomed(records, min_tests=3, top_n=5),
            "nonGroomed": _top_non_groomed(records, min_tests=3, top_n=5)
        },
        "recentTests": _compute_recent_tests(records, page, page_size=_safe_page_size(page_size)),
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
            x["sumScore"] += r["score"]; x["scored"] += 1
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
