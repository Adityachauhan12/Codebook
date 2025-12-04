from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Form
from typing import Optional
from .grooming_client import GroomingClient
from logger_config import get_logger
import base64

router = APIRouter(prefix="/api/grooming", tags=["grooming"])
logger = get_logger("grooming_routes")

@router.get("/insights")
async def get_insights(
    days: Optional[int] = Query(None, description="Number of days"),
    page: int = Query(1, description="Page number"),
    pageSize: int = Query(20, description="Page size")
):
    """Get grooming insights"""
    try:
        client = GroomingClient()
        result = await client.get_insights(days=days, page=page, page_size=pageSize)
        return result
    except Exception as e:
        logger.error(f"Error getting insights: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/insightsByDay")
async def get_insights_by_day(days: int = Query(..., description="Number of days")):
    """Get grooming insights by day"""
    try:
        client = GroomingClient()
        result = await client.get_insights_by_day(days)
        return result
    except Exception as e:
        logger.error(f"Error getting insights by day: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/insightsByIGA")
async def get_insights_by_iga(
    igaCode: str = Query(..., description="IGA Code (required)"),
    crewName: Optional[str] = Query(None, description="Crew Name"),
    dateFrom: str = Query(..., description="Date From (YYYY-MM-DD)"),
    dateTo: str = Query(..., description="Date To (YYYY-MM-DD)")
):
    """Get grooming insights by IGA"""
    try:
        client = GroomingClient()
        result = await client.get_insights_by_iga(igaCode, crewName, dateFrom, dateTo)
        return result
    except Exception as e:
        logger.error(f"Error getting insights by IGA: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/assessment/video")
async def assess_grooming_video(
    video: UploadFile = File(...),
    name: str = Form(...),
    iga_code: str = Form(...),
    base: Optional[str] = Form(None),
    terminal: Optional[str] = Form(None)
):
    """Assess grooming from video"""
    try:
        if not video.content_type or not video.content_type.startswith("video/"):
            raise HTTPException(status_code=400, detail="Only video files are allowed")
        
        file_data = await video.read()
        client = GroomingClient()
        result = await client.check_grooming_video(file_data, video.filename, name, iga_code, base, terminal)
        return result
    except Exception as e:
        logger.error(f"Error assessing video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/assessment/photo")
async def assess_grooming_photo(
    file: UploadFile = File(...),
    crewName: Optional[str] = Form(None),
    igaCode: Optional[str] = Form(None),
    base: Optional[str] = Form(None),
    terminal: Optional[str] = Form(None),
    department: Optional[str] = Form(None)
):
    """Assess grooming from photo"""
    try:
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Only image files are allowed")
        
        file_data = await file.read()
        image_base64 = f"data:{file.content_type};base64,{base64.b64encode(file_data).decode('utf-8')}"
        
        client = GroomingClient()
        result = await client.check_grooming_photo(image_base64, crewName, igaCode, base, terminal, department)
        return result
    except Exception as e:
        logger.error(f"Error assessing photo: {e}")
        raise HTTPException(status_code=500, detail=str(e))
