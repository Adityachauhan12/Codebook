from __future__ import annotations
import os, json, re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

from google.cloud import storage

from gcs_utils import GCS_BUCKET_NAME, GCS_BASE_FOLDER
# Try to import parser from grooming_utils; if missing, use a local fallback
try:
    from grooming_utils import parse_grooming_text  # type: ignore
except Exception:
    import re
    _PARSE_MAP = {
        "overall": re.compile(r"^Overall\s+Assessment:\s*(.+)$", re.I | re.M),
        "score":   re.compile(r"^Overall\s+Score:\s*([0-9]+(?:\.[0-9]+)?)", re.I | re.M),
        "hair":    re.compile(r"^-+\s*Hairstyle:\s*(.+)$", re.I | re.M),
        "makeup":  re.compile(r"^-+\s*Makeup:\s*(.+)$", re.I | re.M),
        "nails":   re.compile(r"^-+\s*Nails:\s*(.+)$", re.I | re.M),
        "acc":     re.compile(r"^-+\s*Accessories:\s*(.+)$", re.I | re.M),
        "uniform": re.compile(r"^-+\s*Uniform:\s*(.+)$", re.I | re.M),
        "issues":  re.compile(r"^Issues\s*Found:\s*(.+)$", re.I | re.M | re.S),
        "reco":    re.compile(r"^Recommendations:\s*(.+)$", re.I | re.M | re.S),
    }
    def parse_grooming_text(text: str) -> dict:
        def _m(key):
            m = _PARSE_MAP[key].search(text or "")
            return m.group(1).strip() if m else ""
        return {
            "overall_assessment": _m("overall"),
            "overall_score": _m("score"),
            "details": {
                "hairstyle": _m("hair"),
                "makeup": _m("makeup"),
                "nails": _m("nails"),
                "accessories": _m("acc"),
                "uniform": _m("uniform"),
            },
            "issues_found": _m("issues"),
            "recommendations": _m("reco"),
        }


DATE_FOLDER = "%Y%m%d"

@dataclass
class Filters:
    start: datetime
    end: datetime
    iga: Optional[str] = None
    crew: Optional[str] = None
    tz_offset_min: int = 330  # default IST

def _to_local(dt: datetime, tz_offset_min: int) -> datetime:
    tz = timezone(timedelta(minutes=tz_offset_min))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)

def _days_in_range(start: datetime, end: datetime) -> List[str]:
    days = []
    d = datetime(start.year, start.month, start.day)
    e = datetime(end.year, end.month, end.day)
    while d <= e:
        days.append(d.strftime(DATE_FOLDER))
        d += timedelta(days=1)
    return days

def _client_bucket() -> Tuple[storage.Client, storage.Bucket]:
    client = storage.Client()
    return client, client.bucket(GCS_BUCKET_NAME)

def _read_json(bucket: storage.Bucket, blob_name: str) -> Optional[dict]:
    blob = bucket.blob(blob_name)
    if not blob.exists():
        return None
    content = blob.download_as_text()
    return json.loads(content)

def _list_names(bucket: storage.Bucket, prefix: str, max_results: Optional[int] = None) -> List[str]:
    names = [b.name for b in bucket.list_blobs(prefix=prefix, max_results=max_results)]
    names.sort()
    return names

def _match_filters(rec: dict, f: Filters) -> bool:
    iga_ok = (not f.iga) or (rec.get("iga_code") == f.iga)
    crew_ok = (not f.crew) or (rec.get("crew_name") == f.crew)
    return iga_ok and crew_ok

def _extract_issues(text: str) -> List[str]:
    if not text:
        return []
    lines = re.split(r"[\n\r]+", text.strip())
    items = []
    for ln in lines:
        ln = re.sub(r"^\s*[-•*\u2022]\s*", "", ln).strip()
        if len(ln) >= 2:
            items.append(ln)
    return items

def _score_from_parsed(parsed: dict) -> Optional[float]:
    try:
        s = parsed.get("overall_score")
        if s is None or s == "":
            return None
        return float(s)
    except Exception:
        return None

def fetch_assessments(filters: Filters, limit: int, offset: int, order_by: str = "timestamp_desc") -> Tuple[List[dict], int]:
    client, bucket = _client_bucket()
    rows: List[dict] = []

    for day in _days_in_range(filters.start, filters.end):
        results_prefix = f"{GCS_BASE_FOLDER}/{day}/results/"
        result_blob_names = _list_names(bucket, results_prefix)
        for blob_name in result_blob_names:
            if not blob_name.endswith(".json"):
                continue
            doc = _read_json(bucket, blob_name)
            if not doc:
                continue

            crew_name = doc.get("crew_name") or "Unknown"
            iga_code = doc.get("iga_code") or "Unknown"
            if not _match_filters({"crew_name": crew_name, "iga_code": iga_code}, filters):
                continue

            ts = doc.get("timestamp") or datetime.now().isoformat()
            try:
                ts_dt = datetime.fromisoformat(ts.replace("Z","+00:00"))
            except Exception:
                ts_dt = datetime.now(timezone.utc)
            ts_local = _to_local(ts_dt, filters.tz_offset_min)

            full_text = doc.get("full_text") or doc.get("result_text") or ""
            parsed = doc.get("parsed")
            if not parsed:
                parsed = parse_grooming_text(full_text) if full_text else {
                    "overall_assessment": "",
                    "overall_score": "",
                    "details": {"hairstyle":"","makeup":"","nails":"","accessories":"","uniform":""},
                    "issues_found": "",
                    "recommendations": ""
                }

            row = {
                "timestamp": ts_local.isoformat(),
                "date": ts_local.date().isoformat(),
                "crew_name": crew_name,
                "iga_code": iga_code,
                "overall_assessment": parsed.get("overall_assessment", ""),
                "overall_score": _score_from_parsed(parsed),
                "image_gcs_path": doc.get("image_gcs_path"),
                "result_text_gcs_path": f"gs://{bucket.name}/{blob_name}" if "grooming_result_" in os.path.basename(blob_name) else None,
                "structured_gcs_path": f"gs://{bucket.name}/{blob_name}" if "grooming_result_structured_" in os.path.basename(blob_name) else None,
                "parsed": parsed,
            }
            rows.append(row)

    total = len(rows)
    reverse = (order_by.lower() == "timestamp_desc")
    rows.sort(key=lambda r: r.get("timestamp",""), reverse=reverse)

    paged = rows[offset: offset + limit] if limit is not None else rows
    return paged, total

def fetch_liveliness_success(filters: Filters) -> int:
    client, bucket = _client_bucket()
    count = 0
    for day in _days_in_range(filters.start, filters.end):
        prefix = f"{GCS_BASE_FOLDER}/{day}/crew_"
        names = _list_names(bucket, prefix)
        for name in names:
            doc = _read_json(bucket, name)
            if not doc:
                continue
            if not _match_filters(doc, filters):
                continue
            for ev in doc.get("assessments", []):
                if ev.get("type") == "liveliness_frame" and ev.get("liveliness_status") == "LIVE":
                    count += 1
    return count

def read_recent_tickets(limit: int = 100) -> List[dict]:
    client, bucket = _client_bucket()
    blob = f"{GCS_BASE_FOLDER}/grooming_tickets_log.json"
    log = _read_json(bucket, blob) or []
    return log[-limit:]

def compute_analytics(records: List[dict], filters: Filters) -> Dict[str, Any]:
    kpis = {
        "total_grooming_assessments": len(records),
        "compliance_rate": 0.0,
        "avg_score": 0.0,
    }
    compliant = 0
    scores: List[float] = []
    daily: Dict[str, Dict[str, Any]] = {}
    per_crew: Dict[Tuple[str,str], Dict[str, Any]] = {}
    issue_counts: Dict[str, int] = {}

    for r in records:
        date = r["date"]
        oa = (r.get("overall_assessment") or "").strip().upper()
        sc = r.get("overall_score")
        if oa == "COMPLIANT":
            compliant += 1
        if isinstance(sc, (int,float)):
            scores.append(float(sc))

        d = daily.setdefault(date, {"assessments": 0, "compliant": 0, "non_compliant": 0, "scores": []})
        d["assessments"] += 1
        if oa == "COMPLIANT":
            d["compliant"] += 1
        else:
            d["non_compliant"] += 1
        if isinstance(sc, (int,float)):
            d["scores"].append(float(sc))

        key = (r.get("crew_name") or "Unknown", r.get("iga_code") or "Unknown")
        pc = per_crew.setdefault(key, {"assessments": 0, "scores": []})
        pc["assessments"] += 1
        if isinstance(sc, (int,float)):
            pc["scores"].append(float(sc))

        parsed = r.get("parsed") or {}
        issues_block = parsed.get("issues_found") or ""
        for one in _extract_issues(issues_block):
            issue_counts[one] = issue_counts.get(one, 0) + 1

    total = len(records)
    if total > 0:
        kpis["compliance_rate"] = round(compliant / total, 4)
    if scores:
        kpis["avg_score"] = round(sum(scores) / len(scores), 2)

    timeseries = []
    for day, agg in sorted(daily.items()):
        avg = round(sum(agg["scores"]) / len(agg["scores"]), 2) if agg["scores"] else 0.0
        timeseries.append({
            "date": day,
            "assessments": agg["assessments"],
            "avg_score": avg,
            "compliant": agg["compliant"],
            "non_compliant": agg["non_compliant"]
        })

    leaders = []
    for (crew_name, iga), val in per_crew.items():
        if val["scores"]:
            leaders.append({
                "crew_name": crew_name, "iga_code": iga,
                "avg_score": round(sum(val["scores"])/len(val["scores"]), 2),
                "assessments": val["assessments"]
            })
    leaders.sort(key=lambda x: (x["avg_score"], x["assessments"]), reverse=True)
    top_performers = leaders[:10]

    most_tested = sorted(
        [{"crew_name": k[0], "iga_code": k[1], "assessments": v["assessments"]} for k, v in per_crew.items()],
        key=lambda x: x["assessments"], reverse=True
    )[:10]

    top_issues = sorted([{"issue": k, "count": v} for k, v in issue_counts.items()],
                        key=lambda x: x["count"], reverse=True)[:15]

    cat_map = {"uniform": ["uniform","stocking","tunic","scarf","badge"],
               "hairstyle": ["hair","ponytail","bun","braid","fringe","highlight"],
               "makeup": ["makeup","foundation","concealer","lip","mascara","eyeshadow","kajal","liner"],
               "nails": ["nail","manicure","polish"],
               "accessories": ["ring","earring","bangle","watch","jewelry","piercing"]}
    cat_counts = {c: 0 for c in cat_map}
    for issue, ct in issue_counts.items():
        l = issue.lower()
        for c, keys in cat_map.items():
            if any(k in l for k in keys):
                cat_counts[c] += ct
                break
    category_stats = {c: {"avg_score": None, "non_compliant": cat_counts[c]} for c in cat_map}

    return {
        "kpis": kpis,
        "top_non_compliance": top_issues,
        "top_performers": top_performers,
        "most_tested_crew": most_tested,
        "category_stats": category_stats,
        "timeseries_daily": timeseries
    }
