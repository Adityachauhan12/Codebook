# Complete unified liveliness + grooming + analytics API

from __future__ import annotations
import os, re, json, base64, shutil
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dotenv import load_dotenv

from fastapi import FastAPI, File, Form, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

import config

from grooming_utils import check_grooming, check_grooming_from_video

try:
    from grooming_utils import assess_image_return_structured, parse_grooming_text
    HAS_ASSESS_STRUCTURED = True
except:
    HAS_ASSESS_STRUCTURED = False

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
    return m is not None, m.group(1).strip() if m else ""

def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except:
        return None

def _lines_to_list(block: str) -> list[str]:
    out = []
    for ln in (block or "").splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.startswith("-"):
            out.append(t.lstrip("- ").strip())
        elif t[0:2].isdigit() or t.startswith(("1.", "2.", "3.", "4.", "5.")):
            out.append(t.split(".",1)[-1].strip() if "." in t else t)
    return out

def _normalize_assessment(a: Optional[str]) -> Optional[str]:
    if a is None:
        return None
    a = a.strip().upper()
    return a if a in ("COMPLIANT", "NON-COMPLIANT") else None

def _parse_text_to_ui(text: str, name: Optional[str], iga: Optional[str]) -> Dict[str,Any]:
    parsed = {}
    try:
        from grooming_utils import parse_grooming_text
        parsed = parse_grooming_text(text) or {}
    except:
        pass

    a_parsed = parsed.get("overall_assessment")
    ok_assess, a_text = _extract(_rx["overall_assessment"], text)
    assessment = _normalize_assessment(a_parsed or (a_text if ok_assess else None))

    s_parsed = parsed.get("overall_score")
    ok_score, s_text = _extract(_rx["overall_score"], text)
    score = (_num(s_parsed) if s_parsed is not None else (_num(s_text) if ok_score else None))

    details = parsed.get("details",{}).copy()
    for key, rx_key in [("uniform","uniform_detail"), ("hairstyle","hair_detail"),
                        ("makeup","makeup_detail"), ("nails","nails_detail"), ("accessories","acc_detail")]:
        if key not in details:
            _, v = _extract(_rx[rx_key], text)
            details[key] = v

    # parse category scores if available else None
    cats = {}
    for c, rx_c, mx in [("uniform","uniform_score",3), ("nails","nails_score",1),
                        ("hairstyle","hairstyle_score",2), ("makeup","makeup_score",2), ("accessories","accessories_score",2)]:
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


def _assess_text_from_image(b64: str) -> str:
    if 'assess_image_return_structured' in globals():
        try:
            return assess_image_return_structured(b64)["full_text"]  # type: ignore
        except:
            pass
    return check_grooming(b64)

from grooming_utils import check_grooming, check_grooming_from_video
from fastapi import BackgroundTasks

from gcs_utils import upload_image_bytes, upload_grooming_text, append_event_to_log, create_ticket, GCS_BUCKET_NAME, GCS_BASE_FOLDER

app = FastAPI(title="Unified Grooming + Analytics API", version="1.0.0")

app.add_middleware(CORSMiddleware,
                   allow_origins=[o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")],
                   allow_credentials=True,
                   allow_methods=["*"],
                   allow_headers=["*"])

class GroomingRequest(BaseModel):
    imageBase64: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None


@app.get("/healthz")
async def healthz():
    return {"status":"ok","time":datetime.now().isoformat()}

@app.post("/check-grooming")
async def check_grooming(payload: GroomingRequest):
    if not payload.imageBase64 or len(payload.imageBase64) < 10:
        return JSONResponse({"error": "Invalid image"}, status_code=400)
    try:
        b64 = payload.imageBase64.split(",")[-1]
        imgbytes = base64.b64decode(b64)
        report = _assess_text_from_image(b64)
        parsed = _parse_text_to_ui(report, payload.crewName, payload.igaCode)

        img_path = upload_image_bytes(imgbytes, payload.igaCode, payload.crewName, "image")

        upload_grooming_text(report, payload.crewName, payload.igaCode, img_path)
        append_event_to_log({"type":"image","parsed":parsed, "image_path":img_path}, payload.crewName, payload.igaCode)
        create_ticket({"type":"image","igaCode":payload.igaCode, "crewName":payload.crewName, "image_path":img_path})

        return {"status":"ok", "result":parsed}
    except Exception as e:
        return JSONResponse({"error":str(e)}, status_code=500)


@app.post("/check-grooming-video")
async def check_grooming_video(video: UploadFile=File(...), name: str=Form(...), igaCode: str=Form(...)):
    try:
        folder = "uploads/videos"
        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, video.filename)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(video.file, f)

        report = check_grooming_from_video(file_path, name, igaCode)
        parsed = _parse_text_to_ui(report, name, igaCode)

        upload_grooming_text(report, name, igaCode, None)
        append_event_to_log({"type":"video","parsed":parsed}, name, igaCode)
        create_ticket({"type":"video","igaCode":igaCode,"crewName":name})

        return {"status":"ok", "result":parsed}
    except Exception as e:
        return JSONResponse({"error":str(e)}, status_code=500)


# Analytics endpoints (similar format for full flexibility)
from analytics_serice import Filters, fetch_assessments, compute_analytics, fetch_liveliness_success

@app.get("/analytics")
async def analytics_summary(
  startDate: str = Query(..., description="YYYY-MM-DD"),
  endDate: str = Query(..., description="YYYY-MM-DD"),
  iga: Optional[str] = None,
  crew: Optional[str] = None,
  limitDays: int = 45
):
    start = datetime.strptime(startDate, "%Y-%m-%d")
    end = datetime.strptime(endDate, "%Y-%m-%d")
    if (end - start).days > limitDays:
        end = start + timedelta(days=limitDays)
    f = Filters(start=start, end=end, iga=iga, crew=crew)

    records, _ = fetch_assessments(f, limit=10_000, offset=0, order_by="timestamp_asc")
    summary = compute_analytics(records, f)
    summary.setdefault("kpis", {})["total_liveliness_success"] = fetch_liveliness_success(f)

    return {
        "range": {"startDate": startDate, "endDate": endDate},
        "filters": {"iga": iga, "crew": crew},
        **summary
    }

@app.get("/tables")
async def analytics_tables(
  startDate: str = Query(..., description="YYYY-MM-DD"),
  endDate: str = Query(..., description="YYYY-MM-DD"),
  iga: Optional[str] = None,
  crew: Optional[str] = None,
  limit: int = 50,
  offset: int = 0,
  order: str = "timestamp_desc"
):
    start = datetime.strptime(startDate, "%Y-%m-%d")
    end = datetime.strptime(endDate, "%Y-%m-%d")
    f = Filters(start=start, end=end, iga=iga, crew=crew)

    records, total = fetch_assessments(f, limit=limit, offset=offset, order_by=order)

    return {
        "range": {"startDate": startDate, "endDate": endDate},
        "filters": {"iga": iga, "crew": crew},
        "total": total,
        "rows": records
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("liveliness_api:app", host="0.0.0.0", port=config.PORT, reload=True)
