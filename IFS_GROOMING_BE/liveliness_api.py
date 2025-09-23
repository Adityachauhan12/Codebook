# liveliness_api.py (image + video + analytics; clean UI object; no UNKNOWN badge)

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

import config  # has PORT etc.

# Grooming core (returns plain text that follows the enforced output format)
from grooming_utils import check_grooming, check_grooming_from_video

# Optional helper if present in your project
try:
    from grooming_utils import assess_image_return_structured, parse_grooming_text  # type: ignore
    HAS_ASSESS_IMAGE_STRUCTURED = True
except Exception:
    HAS_ASSESS_IMAGE_STRUCTURED = False

# ---------------- Regex map for robust parsing ----------------
_rx = {
    "overall_assessment": re.compile(r"^Overall\s*Assessment\s*:\s*(.+)$", re.I | re.M),
    "overall_score":     re.compile(r"Overall\s*Score\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10", re.I),

    # category numeric scores
    "uniform_score":     re.compile(r"Uniform\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*3", re.I),
    "nails_score":       re.compile(r"Nails\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*1", re.I),
    "hairstyle_score":   re.compile(r"Hairstyle\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "makeup_score":      re.compile(r"Makeup\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),
    "accessories_score": re.compile(r"Accessories\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*2", re.I),

    # details text
    "hair_detail":       re.compile(r"-\s*Hairstyle\s*:\s*(.+)$", re.I | re.M),
    "makeup_detail":     re.compile(r"-\s*Makeup\s*:\s*(.+)$", re.I | re.M),
    "nails_detail":      re.compile(r"-\s*Nails\s*:\s*(.+)$", re.I | re.M),
    "acc_detail":        re.compile(r"-\s*Accessories\s*:\s*(.+)$", re.I | re.M),
    "uniform_detail":    re.compile(r"-\s*Uniform\s*:\s*(.+)$", re.I | re.M),

    # blocks
    "issues_block":      re.compile(r"Issues\s*Found\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
    "reco_block":        re.compile(r"Recommendations\s*:\s*(.+?)(?:\n\n|$)", re.I | re.S),
}

def _extract(pat: re.Pattern, text: str) -> Tuple[bool, str]:
    m = pat.search(text or "")
    return (m is not None, m.group(1).strip() if m else "")

def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return None

def _lines_to_list(block: str) -> list[str]:
    out = []
    for ln in (block or "").splitlines():
        t = ln.strip()
        if not t: continue
        if t.startswith("-"):
            out.append(t.lstrip("- ").strip())
        elif t[0:2].isdigit() or t.startswith(("1.", "2.", "3.", "4.", "5.")):
            out.append(t.split(".", 1)[-1].strip() if "." in t else t)
    return out

def _normalize_assessment(a: str | None) -> Optional[str]:
    a = (a or "").strip().upper()
    return a if a in ("COMPLIANT", "NON-COMPLIANT") else None

def _parse_text_to_ui(full_text: str, crew_name: str | None = None, iga_code: str | None = None) -> Dict[str, Any]:
    parsed_details = {}
    try:
        from grooming_utils import parse_grooming_text  # optional project parser
        parsed_details = parse_grooming_text(full_text) or {}
    except Exception:
        parsed_details = {}

    a_from_parsed = parsed_details.get("overall_assessment")
    ok_assess, a_from_text = _extract(_rx["overall_assessment"], full_text)
    assessment = _normalize_assessment(a_from_parsed or (a_from_text if ok_assess else None))

    s_from_parsed = parsed_details.get("overall_score")
    ok_score, s_from_text = _extract(_rx["overall_score"], full_text)
    score = _num(s_from_parsed) if s_from_parsed not in (None, "") else (_num(s_from_text) if ok_score else None)

    details = (parsed_details.get("details") or {}).copy()
    if "uniform" not in details:    _, v = _extract(_rx["uniform_detail"], full_text);    details["uniform"] = v
    if "hairstyle" not in details:  _, v = _extract(_rx["hair_detail"], full_text);       details["hairstyle"] = v
    if "makeup" not in details:     _, v = _extract(_rx["makeup_detail"], full_text);      details["makeup"] = v
    if "nails" not in details:      _, v = _extract(_rx["nails_detail"], full_text);       details["nails"] = v
    if "accessories" not in details:_, v = _extract(_rx["acc_detail"], full_text);         details["accessories"] = v

    ok, v = _extract(_rx["uniform_score"], full_text);     uniform = _num(v) if ok else None
    ok, v = _extract(_rx["nails_score"], full_text);       nails = _num(v) if ok else None
    ok, v = _extract(_rx["hairstyle_score"], full_text);   hairstyle = _num(v) if ok else None
    ok, v = _extract(_rx["makeup_score"], full_text);      makeup = _num(v) if ok else None
    ok, v = _extract(_rx["accessories_score"], full_text); accessories = _num(v) if ok else None

    ok, blk = _extract(_rx["issues_block"], full_text); issues = _lines_to_list(blk) if ok else []
    ok, blk = _extract(_rx["reco_block"], full_text);   recommendations = _lines_to_list(blk) if ok else []

    return {
        "person": {"name": crew_name or "", "iga_code": iga_code or ""},
        "assessment": assessment,                       # None when unknown (UI hides badge)
        "score": score,                                 # None when missing
        "scores": {
            "uniform": uniform, "nails": nails, "hairstyle": hairstyle, "makeup": makeup, "accessories": accessories
        },
        "details": details,
        "issues": issues,
        "recommendations": recommendations,
    }

def _assess_image_to_text(image_b64: str) -> str:
    if 'assess_image_return_structured' in globals():
        try:
            return assess_image_return_structured(image_b64)["full_text"]  # type: ignore
        except Exception:
            pass
    return check_grooming(image_b64)

# ----------- GCS helpers -----------
from gcs_utils import (
    upload_image_bytes,
    upload_grooming_result_text,
    append_event_to_crew_log,
    create_ticket,
    GCS_BUCKET_NAME,  # noqa
    GCS_BASE_FOLDER,  # noqa
)

# ----------- FastAPI app -----------
app = FastAPI(title="IFS Grooming + Analytics API (single app)", version="2.5.0")

ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOW_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------- Models -----------
class GroomingRequest(BaseModel):
    imageBase64: str
    crewName: Optional[str] = None
    igaCode: Optional[str] = None

# ----------- Health -----------
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": datetime.now().isoformat()}

# ----------- Image endpoint -----------
@app.post("/check-grooming")
async def check_grooming_endpoint(payload: GroomingRequest):
    try:
        b64 = payload.imageBase64 or ""
        if not isinstance(b64, str) or len(b64) < 10:
            return JSONResponse(status_code=400, content={"error": "Invalid image data"})
        clean_b64 = b64.split(",")[-1]
        img_bytes = base64.b64decode(clean_b64)

        full_text = _assess_image_to_text(clean_b64)
        ui_result = _parse_text_to_ui(full_text, payload.crewName, payload.igaCode)

        image_gcs_path = upload_image_bytes(
            img_bytes,
            iga_code=(payload.igaCode or "Unknown"),
            crew_name=payload.crewName,
            kind="grooming_image",
        )
        upload_grooming_result_text(
            result_text=full_text,
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
            image_gcs_path=image_gcs_path,
        )
        append_event_to_crew_log(
            {"type": "grooming_image", "parsed": ui_result, "image_gcs_path": image_gcs_path},
            crew_name=payload.crewName,
            iga_code=payload.igaCode,
        )
        _ = create_ticket({"event": "grooming_image", "iga_code": payload.igaCode, "crew_name": payload.crewName})

        return {"status": "ok", "result": ui_result}
    except Exception as e:
        print(f"X Error in /check-grooming: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ----------- Video endpoint -----------
@app.post("/check-grooming-video")
async def check_grooming_video_endpoint(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...),
):
    try:
        video_dir = "uploads/videos"
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, video.filename)
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        full_text = check_grooming_from_video(video_path, name, iga_code)
        ui_result = _parse_text_to_ui(full_text, name, iga_code)

        upload_grooming_result_text(result_text=full_text, crew_name=name, iga_code=iga_code, image_gcs_path=None)
        append_event_to_crew_log({"type": "grooming_video", "parsed": ui_result}, crew_name=name, iga_code=iga_code)
        _ = create_ticket({"event": "grooming_video", "iga_code": iga_code, "crew_name": name})

        return {"status": "ok", "result": ui_result}
    except Exception as e:
        print(f"X Error in /check-grooming-video: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ----------- Analytics endpoints (DIRECT on this app) -----------
# Bring service-layer helpers
from analytics_serice import (  # file name intentionally 'serice' per your file
    Filters,
    fetch_assessments,
    compute_analytics,
    fetch_liveliness_success,
)

def _parse_date(s: str):
    return datetime.strptime(s, "%Y-%m-%d").date()

@app.get("/analytics")
async def analytics_summary(
    startDate: str = Query(..., description="YYYY-MM-DD"),
    endDate: str = Query(..., description="YYYY-MM-DD"),
    iga: Optional[str] = None,
    crew: Optional[str] = None,
    limitDays: int = 45,
):
    start = _parse_date(startDate)
    end = _parse_date(endDate)
    if (end - start).days > limitDays:
        end = start + timedelta(days=limitDays)

    f = Filters(start=start, end=end, iga=iga, crew=crew)
    records, _ = fetch_assessments(f, limit=10_000, offset=0, order_by="timestamp_asc")
    summary = compute_analytics(records, f)
    summary.setdefault("kpis", {})["total_liveliness_success"] = fetch_liveliness_success(f)

    return {
        "range": {"startDate": startDate, "endDate": endDate},
        "filters": {"iga": iga, "crew": crew},
        **summary,
    }

@app.get("/tables")
async def analytics_tables(
    startDate: str = Query(..., description="YYYY-MM-DD"),
    endDate: str = Query(..., description="YYYY-MM-DD"),
    iga: Optional[str] = None,
    crew: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "timestamp_desc",  # or "timestamp_asc"
):
    start = _parse_date(startDate)
    end = _parse_date(endDate)
    f = Filters(start=start, end=end, iga=iga, crew=crew)
    rows, total = fetch_assessments(f, limit=limit, offset=offset, order_by=order)

    return {
        "range": {"startDate": startDate, "endDate": endDate},
        "filters": {"iga": iga, "crew": crew},
        "total": total,
        "rows": rows,
    }

# ----------- Main -----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("liveliness_api:app", host="0.0.0.0", port=config.PORT, reload=True)
