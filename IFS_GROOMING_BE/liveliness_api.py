# main.py — Grooming checks + Insights/Search APIs (FastAPI)

from __future__ import annotations

import os
import re
import base64
import shutil
from datetime import datetime, timedelta, date as _date
from typing import Optional, Dict, Any, Tuple, List

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, Query
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
    "overall_score": re.compile(r"Overall\s*Score\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10", re.I),
    "uniform_score": re.compile(r"Uniform\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*3", re.I),
    "nails_score": re.compile(r"Nails\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*1", re.I),
    "hairstyle_score": re.compile(r"Hairstyle\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "makeup_score": re.compile(r"Makeup\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "accessories_score": re.compile(r"Accessories\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "hair_detail": re.compile(r"-\s*Hairstyle\s*:\s*(.+)$", re.I | re.M),
    "makeup_detail": re.compile(r"-\s*Makeup\s*:\s*(.+)$", re.I | re.M),
    "nails_detail": re.compile(r"-\s*Nails\s*:\s*(.+)$", re.I | re.M),
    "acc_detail": re.compile(r"-\s*Accessories\s*:\s*(.+)$", re.I | re.M),
    "uniform_detail": re.compile(r"-\s*Uniform\s*:\s*(.+)$", re.I | re.M),
    "issues_block": re.compile(r"Issues\s*Found\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
    "reco_block": re.compile(r"Recommendations\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
}


def _extract(pat: re.Pattern, text: str) -> Tuple[bool, str]:
    m = pat.search(text or "")
    return (m is not None, m.group(1).strip() if m else "")


def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return None


def _lines_to_list(block: str) -> List[str]:
    out = []
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
    ok_assess, a_text = _extract(_rx["overall_assessment"], text)
    ok_score, s_text = _extract(_rx["overall_score"], text)
    score = _num(s_text) if ok_score else None
    assessment = _normalize_assessment(a_text if ok_assess else None)

    details: Dict[str, str] = {}
    for key, rx_key in [
        ("uniform", "uniform_detail"),
        ("hairstyle", "hair_detail"),
        ("makeup", "makeup_detail"),
        ("nails", "nails_detail"),
        ("accessories", "acc_detail"),
    ]:
        _, v = _extract(_rx[rx_key], text)
        details[key] = v

    cats: Dict[str, Optional[float]] = {}
    for c, rx_c in [
        ("uniform", "uniform_score"),
        ("nails", "nails_score"),
        ("hairstyle", "hairstyle_score"),
        ("makeup", "makeup_score"),
        ("accessories", "accessories_score"),
    ]:
        ok, val = _extract(_rx[rx_c], text)
        cats[c] = _num(val) if ok else None

    ok_i, block_i = _extract(_rx["issues_block"], text)
    ok_r, block_r = _extract(_rx["reco_block"], text)

    return {
        "person": {"name": name or "", "iga_code": iga or ""},
        "assessment": assessment,
        "score": score,
        "scores": cats,
        "details": details,
        "issues": _lines_to_list(block_i) if ok_i else [],
        "recommendations": _lines_to_list(block_r) if ok_r else [],
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
    base: Optional[str] = None          # currently unused; kept for forward compatibility
    department: Optional[str] = None    # currently unused


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
        # NOTE: upload_grooming_result_text signature (result_text, crew_name, iga_code, image_gcs_path=None, now=None)
        upload_grooming_result_text(report, payload.crewName, payload.igaCode, img_path)
        append_event_to_crew_log(
            {"type": "image", "parsed": parsed, "image_path": img_path},
            payload.crewName,
            payload.igaCode,
        )
        create_ticket({"type": "image", "igaCode": payload.igaCode, "crewName": payload.crewName, "image_path": img_path})

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
    try:
        video_dir = "uploads/videos"
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, video.filename)
        with open(video_path, "wb") as f:
            shutil.copyfileobj(video.file, f)

        full_text = check_grooming_from_video(video_path, name, iga_code)
        parsed = _parse_text_to_ui(full_text, name, iga_code)

        upload_grooming_result_text(full_text, name, iga_code, None)
        append_event_to_crew_log({"type": "video", "parsed": parsed}, name, iga_code)
        create_ticket({"type": "video", "iga_code": iga_code, "crew_name": name})

        return {"status": "ok", "result": parsed}
    except Exception as e:
        print(f"Error in /check-grooming-video: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------- The 3 requested APIs ----------------
from dashboard_service import get_insights, get_info, search_people  # noqa: E402


def _resolve_range(dateFrom: Optional[_date], dateTo: Optional[_date], days: Optional[int]):
    """
    Resolve final date range.
    If days is supplied (e.g., 7), returns last N days including today.
    Else uses dateFrom/dateTo. Defaults to last 7 days.
    """
    today = datetime.utcnow().date()
    if days and int(days) > 0:
        d = int(days)
        _from = today - timedelta(days=d - 1)
        _to = today
        return _from, _to
    _from = dateFrom or (today - timedelta(days=6))
    _to = dateTo or today
    return _from, _to


# 1) MAIN insights — grooming vs non-grooming, categories, daily hover graph, top 5 groomed/non-groomed, recent tests
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


# 2) General info box — cards-like KPIs with base=null
@app.get("/v1/info")
async def info_box(
    dateFrom: Optional[_date] = Query(None),
    dateTo: Optional[_date] = Query(None),
    days: Optional[int] = Query(None),
):
    _from, _to = _resolve_range(dateFrom, dateTo, days)
    return get_info(_from, _to)


# 3) Search — by IGA code or crew name (case-insensitive), paginated
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


# ---------------- Entrypoint ----------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=True)
