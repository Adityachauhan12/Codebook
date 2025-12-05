# import truststore

# truststore.inject_into_ssl()


from concurrent.futures import ThreadPoolExecutor
import re
from uuid import uuid4

from FlightReport.ai_analytic_bigQuery import compute_combined_summary
from FlightReport.core_bigquery import category_details_sql, compute_dashboard_sql, day_details_sql, flight_details_sql, route_details_sql
from Documentation.workflow import documentationAgent
# from Documentation.workflow import documentationAgent
from main_functions import JWTAuthMiddleware, SecurityHeadersMiddleware
import jwt
from rotate_pdf import detect_rotation_template_matching, save_corrected_pdf
from storage_clients import get_bucket, get_firestore_client
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiohttp
from io import BytesIO
import json
from datetime import datetime, timedelta, timezone
import time
import os
from google.cloud import storage
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from pydantic import BaseModel,Field
import google.generativeai as genai
from CrewProfile.crew_profile import fetch_and_merge_crew_data, generate_and_store_insights, load_filtered_iga_data,insights_cache

from FlightReport.dashboard import apply_filters, compute_dashboard
from FlightReport.dashboard import generate_enhanced_service_analytics
from GenericAISearch.tools import GeminiClient
from OCR.doc_extractor import doc_extractor
from google.cloud.firestore_v1 import ArrayUnion

# from OCR.tools import fix_pdf_orientation
# from OCR.tools import fix_pdf_orientation
# from OCR.tools import fix_pdf_orientation
# from OCR.tools import fix_pdf_orientation
from TrendingSearches.suggestion_genai import extract_top_queries
from URTI_Leaves.leaves import  decode_json_fields, fetch_crew_info, query_rows, table_ref, upsert_leave_record, query_paginated_leaves
from joc import get_flight_plan
from Top10CDP import getCustomerDetails
# from FlightReport import AI_Insights_API
from logger_config import get_logger
from typing import List, Dict, Optional, Sequence, Union, Mapping
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
import swagger_ui_bundle


from google.cloud import storage, bigquery, firestore
from fastmcp import Client
from Records import main
from fastmcp.client.transports import StreamableHttpTransport
import httpx
import secrets
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
from GenericAISearch.workflow import AnalysisWorkflow
from contextlib import asynccontextmanager
from GenericAISearch.workflow import AnalysisWorkflow
from fastapi.responses import StreamingResponse
from Records.main import read_data_from_json, get_expiring_details
import io
import openpyxl
from document_expiry_cron import get_or_update_expiry_data# import ssl
from MailService_payload.payload import get_mail_payload, get_top_expiring

from fastapi.responses import StreamingResponse
# from TrendingSearches.main import clean_text,clustering,pre_processing,suggested_queries
from TrendingSearches.get_file import append_query,read_query,add_results
import ssl



# Import Workflow, Firestore, and other custom modules
from GenericAISearch.ai_search_firestore import (
    fetch_session,
    fetch_all_user_sessions,
    delete_session,
)
from logger_config import get_logger
from GenericAISearch.tools import GeminiClient
from typing import Dict, Any


dist_path = os.path.dirname(swagger_ui_bundle.__file__)

MCP_URL = os.getenv("MCP_URL")
logger = get_logger("app")
INPUT_PATH = "FlightReport/Master_July_31.xlsx"
if not os.path.exists(INPUT_PATH):
    raise FileNotFoundError(f"Data file not found: {INPUT_PATH}")
try:
    wb = openpyxl.load_workbook(INPUT_PATH)
    ws = wb.active
    _data_rows = [row for row in ws.iter_rows(min_row=1, values_only=True)]
    headers = _data_rows[0]
    rows = _data_rows[1:]

    df = pd.DataFrame(rows, columns=headers)
    print(df.head())
except Exception as e:
    logger.exception(f"Failed to load Excel file: {e}")
    raise RuntimeError("Error loading Excel file")

def validate_comment(comment: str) -> str:
    """
    Returns the cleaned comment string.
    Allowed characters: A-Z, a-z, 0-9, comma, @, dot, space.
    All other characters are removed.
    """
    # Keep ONLY allowed characters
    return re.sub(r'[^A-Za-z0-9,@.\s]', '', comment)

def create_custom_httpx_client(**kwargs):
    """
    Factory to create a custom httpx client with SSL verification disabled,
    and a 300s timeout (total read) for MCP requests.
    This single AsyncClient will be used by the StreamableHttpTransport and
    shared by the MCP client across the whole app.
    """
    kwargs["verify"] = False
    # Set an explicit 300s total/read timeout. Keep a reasonable connect timeout.
    # httpx.Timeout(args) signature: Timeout(total=None, connect=None, read=None, write=None, pool=None)
    kwargs.setdefault("timeout", httpx.Timeout(300.0, connect=10.0, read=300.0))
    # Important: re-use the same AsyncClient instance across the app,
    # so create it once and return it (httpx clients are async-context-managers).
    # But StreamableHttpTransport expects a factory, so return a factory-created client.
    return httpx.AsyncClient(**kwargs)

# Create the transport with the custom httpx client factory
transport = StreamableHttpTransport(
    url=MCP_URL,
    httpx_client_factory=create_custom_httpx_client,
)

# Initialize the MCP client with the custom transport (client is module-scoped)
client = Client(transport=transport)

TARGET_QUALS = ["AEP", "PVC", "PASSPORT", "CMC", "WMED", "CMED", "NORSID"]
 



@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application...")
    app.state.executor = ThreadPoolExecutor(max_workers=4)
    # Initialize workflow
    app.state.workflow = AnalysisWorkflow(client=client)
    logger.info("Workflow initialized")

    # Initialize GeminiClient
    app.state.gemini_client = GeminiClient()
    logger.info("GeminiClient initialized")

    # Initialize MCP client
    try:
        await client.__aenter__()
        logger.info("MCP client connected (async context entered)")
    except Exception as e:
        logger.exception("Failed to connect MCP client during startup: %s", e)

    # Initialize and start scheduler
    scheduler = AsyncIOScheduler()
    app.state.scheduler = scheduler
    
    # Schedule jobs
    try:
        # Mail payload update job every day at 6:00 AM (hits API endpoint)
        scheduler.add_job(
            scheduled_mail_payload_api_call,
            'cron',
            hour=6,   # 6 AM
            minute=0,
            id='mail_payload_update_job',
            name='Mail Payload API Update (All Documents)'
        )
        logger.info("[SCHEDULER] Mail payload API update job scheduled for 6:00 AM daily")
        
        # Evening backup job every day at 6:00 PM (hits API endpoint)
        scheduler.add_job(
            scheduled_mail_payload_api_call,
            'cron',
            hour=18,  # 6 PM (18:00 in 24-hour format)
            minute=0,
            id='mail_payload_evening_job',
            name='Mail Payload Evening API Update (All Documents)'
        )
        logger.info("[SCHEDULER] Evening mail payload API update job scheduled for 6:00 PM daily")
        
        # Start the scheduler
        scheduler.start()
        logger.info("[SCHEDULER] Started successfully with FastAPI backend")
        
        # Log scheduled jobs
        for job in scheduler.get_jobs():
            logger.info(f"  - {job.name} (ID: {job.id}) - Next run: {job.next_run_time}")
            
    except Exception as e:
        logger.exception(f"Failed to start scheduler: {e}")

    # ---- yield control to FastAPI ----
    yield

    # ---- teardown section ----
    logger.info("Shutting down application...")
    app.state.executor.shutdown(wait=True)
    # Shutdown scheduler
    try:
        if hasattr(app.state, 'scheduler'):
            app.state.scheduler.shutdown()
            logger.info("[SCHEDULER] Shutdown successfully")
    except Exception as e:
        logger.exception(f"Error shutting down scheduler: {e}")

    # Close Gemini client cleanly
    # try:
    #     await app.state.gemini_client.aclose()
    #     logger.info("GeminiClient closed")
    # except Exception as e:
    #     logger.exception("Error while closing GeminiClient: %s", e)

    # Disconnect MCP client
    try:
        await client.__aexit__(None, None, None)
        logger.info("MCP client disconnected (async context exited)")
    except Exception as e:
        logger.exception("Error while disconnecting MCP client during shutdown: %s", e)



    logger.info("Application shutdown complete")


# Create FastAPI app with lifespan handler
app = FastAPI(lifespan=lifespan)
# app = FastAPI()

# for AD Routes
from sso.routes import router as sso_router
app.include_router(sso_router)
 
# Health route
BUCKET_NAME = "daily-data-dump"
BLOB_PATH = "crew_data/crew_data.json"
# TARGET_QUALS = {"AEP", "PVC", "PAS", "CMC", "WMED", "CMED", "NORSID", "ID"}
BASES = ["DEL", "AMD", "BOM", "CCU", "MAA", "HYD", "LKO", "PNQ", "COK", "IXC", "IDR", "JAI", "BLR"]

GCP_PROJECT_ID = os.getenv("PROJECT_ID")
GCS_BUCKET_NAME = os.getenv("BUCKET_NAME")
GCS_BUCKET_NAME="crew-docs"
FIRESTORE_COLLECTION_ID = "flight_reports"
FIRESTORE_DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID")

# db = firestore.Client(project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE_ID)

# # Reference the collection
# collection_ref = db.collection(FIRESTORE_COLLECTION_ID)

# Consolidated middleware setup

secret_key = os.getenv("SESSION_SECRET_KEY")
app.add_middleware(
    CORSMiddleware,
       allow_origins=["https://6e-ifs-365-dev.goindigo.in", "http://localhost:3000", "https://6e-ifs-365-uat.goindigo.in", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
        "HEAD"
    ],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=secret_key)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(JWTAuthMiddleware)


class AIInsights(BaseModel):
    question: str

class ScheduleRequest(BaseModel):
    startdate: str
    enddate: str
    startStation: str | None = None
    endStation: str | None = None
    flightNumber: int | None = None
    serviceType: str | None = None

class CrewRequest(BaseModel):
    startStation: str | None = None

class TopCustomerRequest(BaseModel):
    startStation: str

class LeaveRequest(BaseModel):
    iga_code: str
    employee_name: str
    base: str
    start_date: str
    end_date: str
    comment: str
    applied_by: Optional[str] = None
    applied_by_name: Optional[str] = None
class StatusUpdatedBy(BaseModel):
    name: str
    iga_code: str
class AdminAction(BaseModel):
    status: int  # 1 for approved, 0 for rejected
    comment: Optional[str] = None
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
    status_updated_by: Optional[StatusUpdatedBy] = None


# --- Startup / Shutdown: ensure MCP client is connected for all request handlers ---

class StatusUpdateRequest(BaseModel):
    id:str
    firestore_doc_id: str
    comment:str
    action: int   # 1 = approve, 0 = reject
    doc_type:str
    approved_by: str | None = ""
    rejected_by: str | None = ""
    

# ------------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=dist_path), name="static")

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Docs",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui.css",
        swagger_favicon_url="/static/favicon.png",
    )
@app.get("/")
async def ifs_server():
    return {"message": "IFS-365 Backend is running."}

@app.get("/health")
async def health_check():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Health check requested.")
    return JSONResponse({
        "timestamp": now,
        "message": "Thank you for logging into IFS-365. Backend services will be up soon."
    })

@app.post("/api/something")
def do_something():
    return {"ok": True}

@app.post("/api/getSchedule")
async def get_schedule(
payload: ScheduleRequest
):
 
    logger.info(f"Fetching schedule from {payload.startdate} to {payload.enddate}")
    try:
        flight_list = await get_flight_plan.get_joc_flight_plan_data(
            client,
            startdate=payload.startdate,
            enddate=payload.enddate,
            startstation=payload.startStation,
            isSchedule=True,
        )
        logger.info(f"Fetched {len(flight_list)} flights.")
        return JSONResponse({"flight_list": flight_list})
    except Exception:
        logger.exception("Error in /getSchedule")
        return JSONResponse({"error": "Failed to fetch schedule."}, status_code=500)

@app.post("/api/getFlightsWithCrew")
async def get_flights_with_crew(payload: CrewRequest):
    """
    Fetch flights with crew data for a given start station.
    Example call:  GET /getFlightsWithCrew?startStation=DEL
    """
    logger.info(f"Fetching flights with crew for station {payload.startStation}")
    try:
        flight_list = await get_flight_plan.get_joc_flight_plan_data(
            client,
            startdate="",
            enddate="",
            startstation=payload.startStation,
            isSchedule=False,
        )
        logger.info(f"Fetched {len(flight_list)} flights with crew data.")
        return JSONResponse({"flight_list": flight_list})
    except Exception:
        logger.exception("Error in /getFlightsWithCrew")
        return JSONResponse({"error": "Failed to fetch flights with crew."}, status_code=500)

@app.post("/api/getTopCustomersPerFlight")
async def get_top_customers_per_flight(
    payload: TopCustomerRequest
):
    """
    Fetch the top customers for flights starting at the given station.
    """
    logger.info(f"Fetching Top Customers for flights starting at {payload.startStation}")
    try:
        data = await getCustomerDetails.get_customer_data(client,payload.startStation)
        logger.info(f"Fetched Top Customer data for {len(data)} flights.")
        return JSONResponse({"CustomerListPerFlight": data})
    except Exception:
        logger.exception("Error in /getTopCustomersPerFlight")
        return JSONResponse(
            {"error": "Failed to fetch top customer data."},
            status_code=500
        )

@app.post("/api/add_query")
async def group_add(query: str):
    logger.info("Received request to /add_query")

    try:
        logger.info(f"Appending query: {query}")
        vals = append_query(query)
        logger.info("Query appended successfully")

        return JSONResponse(
            status_code=200,
            content={"success": True, "data": vals}
        )

    except Exception as e:
        logger.error(f"Error in /Group_add: {str(e)}")
        logger.exception(e)  # full traceback
        raise HTTPException(
            status_code=500,
            detail="Internal server error while adding query"
        )


@app.post("/api/return_trending")
async def group_queries():
    logger.info("Received request to /return_trending")
    
    try:
        logger.info("Reading queries...")
        queries = read_query()

        logger.info(f"Total queries fetched: {len(queries)}")
        Grouped_queries = extract_top_queries(queries)
        logger.info("Suggested grouped queries generated")

        add_results(Grouped_queries)
        logger.info("Grouped queries saved successfully")

        return JSONResponse(
            status_code=200,
            content={"success": True, "trending_searches": Grouped_queries}
        )

    except Exception as e:
        logger.error(f"Error in /Group_Q: {str(e)}")
        # logger.exception(e)  # log full traceback
        
        raise HTTPException(
            status_code=500,
            detail="Error while grouping queries"
        )



@app.get("/expiring_counts")
async def expiring_details(
    query: Optional[Union[int, str]] = Query(None),
    doc: Optional[str] = Query(None),
    base: Optional[str] = Query(None)
) -> Union[Dict[str, List[Dict]], Dict[str, Dict[str, int]]]:
    """
    API endpoint that loads data from JSON and delegates to get_expiring_details.
    """
    logger.info("API call: /expiring_counts with query=%s, doc=%s, base=%s", query, doc, base)
    data = read_data_from_json()
    records = data.get("crew_data", [])
    # Normalize doc to match TARGET_QUALS casing if provided
    if doc:
        # allow either lowercase or uppercase doc values
        doc = next((q for q in TARGET_QUALS if q.lower() == doc.lower()), doc)
    return main.get_expiring_details(records, query, TARGET_QUALS, doc=doc, base=base)




@app.get("/top_expiring")
async def top_expiring(
    search: Optional[str] = Query(None),
    doc: Optional[str] = Query(None),
    base: Optional[str] = Query(None)
) -> Dict[str, List[Dict]]:
    """
    API endpoint that returns top expiring records per qualification.
    """
    logger.info("API call: /top_expiring with search=%s, doc=%s, base=%s", search, doc, base)
    data = read_data_from_json()
    records = data.get("crew_data", [])
    if doc:
        doc = next((q for q in TARGET_QUALS if q.lower() == doc.lower()), doc)
    return main.get_top_expiring(records, TARGET_QUALS, search, doc=doc, base=base)




@app.get("/expiring_csv")
async def download_expiring_csv(
    query: Union[int, str] = Query(...),
    doc: Optional[str] = Query(None),
    base: Optional[str] = Query(None)
):
    """
    Download CSV of expiring or missing qualifications, data from JSON file.
    """
    logger.info("API call: /expiring_csv with query=%s, doc=%s, base=%s", query, doc, base)
    data = read_data_from_json()
    records = data.get("crew_data", [])
    target_quals = list(TARGET_QUALS)
    if doc:
        doc = next((q for q in TARGET_QUALS if q.lower() == doc.lower()), doc)
        if doc in TARGET_QUALS:
            target_quals = [doc]
    csv_buffer, filename = main.generate_expiring_csv(records, target_quals, query, base=base)
    return StreamingResponse(
        csv_buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/mail-payload")
async def get_mail_payload_api():
    """
    API endpoint to retrieve mail payload data for document expiry notifications.
    Returns expiring PVC records for DEL base.
    """
    try:
        logger.info("Fetching mail payload data")
        payload_data = get_mail_payload()
        logger.info(f"Mail payload data retrieved successfully with {len(payload_data.get('PVC', []))} PVC records")
        return JSONResponse({
            "success": True,
            "data": payload_data,
            "message": "Mail payload data retrieved successfully"
        })
    except Exception as e:
        logger.exception("Error retrieving mail payload data")
        return JSONResponse({
            "success": False,
            "error": "Failed to retrieve mail payload data",
            "message": str(e)
        }, status_code=500)
 
class MailPayloadRequest(BaseModel):
    search: str = ""
    doc: str = "PVC"
    base: str = "DEL"
 
class SendMailPayloadRequest(BaseModel):
    search: str = ""
    doc: str = "PVC"
    base: str = "DEL"
    recipient_email: str
    subject: str | None = None
    document_id: str | None = None
 
@app.post("/api/mail-payload")
async def get_custom_mail_payload(request: MailPayloadRequest):
    """
    API endpoint to retrieve custom mail payload data with specified parameters.
   
    Parameters:
    - search: Search term for filtering records (optional)
    - doc: Document type (default: PVC)
    - base: Base location (default: DEL)
    """
    try:
        logger.info(f"Fetching custom mail payload data with search='{request.search}', doc='{request.doc}', base='{request.base}'")
       
        payload_data = get_top_expiring(request.search, request.doc, request.base)
        total_records = sum(len(records) for records in payload_data.values())
       
        logger.info(f"Custom mail payload data retrieved successfully with {total_records} total records")
        return JSONResponse({
            "success": True,
            "data": payload_data,
            "parameters": {
                "search": request.search,
                "doc": request.doc,
                "base": request.base
            },
            "total_records": total_records,
            "message": "Custom mail payload data retrieved successfully"
        })
    except Exception as e:
        logger.exception("Error retrieving custom mail payload data")
        return JSONResponse({
            "success": False,
            "error": "Failed to retrieve custom mail payload data",
            "message": str(e)
        }, status_code=500)
 
@app.post("/api/send-mail-payload")
async def send_mail_payload(request: SendMailPayloadRequest):
    """
    API endpoint to store categorized mail payload data in Firestore based on expiry periods.
    This endpoint retrieves the expiring document data, categorizes by expiry timeline, and stores each category separately.
   
    Categories:
    - 1_month: Documents expiring within 30 days
    - 3_month: Documents expiring between 30-90 days  
    - 7_month: Documents expiring between 90-210 days
   
    Parameters:
    - search: Search term for filtering records (optional)
    - doc: Document type (default: PVC)
    - base: Base location (default: DEL)
    - recipient_email: Email address to send the payload to
    - subject: Email subject (optional)
    """
    try:
        logger.info(f"Storing categorized mail payload for {request.recipient_email}")
       
        # Create firestore client for mail service
        mail_service = get_firestore_client(os.getenv("FIRESTORE_DEFAULT_STORE"))
        collection_ref = mail_service.collection("mail_service")
       
        # Get the payload data using get_expiring_details
        data = read_data_from_json()
        records = data.get("crew_data", [])
        
        # Normalize doc to match TARGET_QUALS casing if provided
        if request.doc:
            normalized_doc = next((q for q in TARGET_QUALS if q.lower() == request.doc.lower()), request.doc)
        else:
            normalized_doc = None
            
        # Get data for all three time periods (1, 3, and 7 months) using a loop
        time_periods = [1, 3, 7]
        period_data_list = []
        
        for period in time_periods:
            print("---------------------------------------", period, TARGET_QUALS, normalized_doc, request.base)
            period_data = get_expiring_details(records, period, TARGET_QUALS, doc=normalized_doc, base=request.base)
            period_data_list.append(period_data)
        
        # Combine all data into a single payload_data structure
        payload_data = {}
        for doc_type in TARGET_QUALS:
            if normalized_doc and doc_type != normalized_doc:
                continue
            payload_data[doc_type] = []
            # Add records from all time periods, avoiding duplicates
            all_records = []
            seen_ids = set()
            
            for period_data in period_data_list:
                if doc_type in period_data:
                    for record in period_data[doc_type]:
                        record_id = record.get('id', record.get('iga_code', ''))
                        if record_id not in seen_ids:
                            all_records.append(record)
                            seen_ids.add(record_id)
            
            payload_data[doc_type] = all_records
       
        # Get current date for comparison
        current_date = datetime.now()
       
        # Function to categorize records based on expiry date
        def categorize_by_expiry(records, doc_type):
            categories = {
                "1_month": [],
                "3_month": [],
                "7_month": []
            }
           
            expiry_field = f"{doc_type.lower()}_expiry_date"
            logger.info(f"Categorizing {len(records)} records for doc_type: {doc_type}, looking for field: {expiry_field}")
           
            for record in records:
                if expiry_field in record and record[expiry_field]:
                    try:
                        # Handle different date formats
                        expiry_str = record[expiry_field]
                        #expiry_date = record[expiry_field]
                       
                        if isinstance(expiry_str, str):
                            if 'T' in expiry_str:
                                # Remove timezone info and parse
                                clean_str = expiry_str.replace('Z', '').replace('+00:00', '')
                                expiry_date = datetime.fromisoformat(clean_str)
                            else:
                                expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d')
                        else:
                            # If it's already a datetime object
                            expiry_date = expiry_str
                       
                        if expiry_date:
                            days_until_expiry = (expiry_date - current_date).days
                            logger.debug(f"Record {record.get('id', 'unknown')}: expiry={expiry_str}, days_until={days_until_expiry}")
                           
                            # Include expired records (negative days) and future records within limits
                            # 1_month: Already expired up to 30 days in future
                            # 3_month: 30-90 days in future  
                            # 7_month: 90-210 days in future
                           
                            if days_until_expiry <= 30:  # This includes expired records (negative values)
                                categories["1_month"].append(record)
                                logger.debug(f"Added to 1_month: {record.get('id', 'unknown')} (days: {days_until_expiry})")
                            elif days_until_expiry <= 90:
                                categories["3_month"].append(record)
                                logger.debug(f"Added to 3_month: {record.get('id', 'unknown')} (days: {days_until_expiry})")
                            elif days_until_expiry <= 210:
                                categories["7_month"].append(record)
                                logger.debug(f"Added to 7_month: {record.get('id', 'unknown')} (days: {days_until_expiry})")
                            else:
                                logger.debug(f"Record {record.get('id', 'unknown')} expires in {days_until_expiry} days, beyond 7 months")
                       
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid date format for record {record.get('id', 'unknown')}: {expiry_str}, error: {e}")
                        continue
                else:
                    logger.debug(f"Record {record.get('id', 'unknown')} missing or null {expiry_field}")
           
            logger.info(f"Categorization results: 1_month={len(categories['1_month'])}, 3_month={len(categories['3_month'])}, 7_month={len(categories['7_month'])}")
            return categories
       
        # Process and categorize data for each document type
        all_categories = {"1_month": [], "3_month": [], "7_month": []}
        total_original_records = 0
       
        for doc_type, records in payload_data.items():
            if records:
                total_original_records += len(records)
                categorized = categorize_by_expiry(records, doc_type)
                for category in all_categories:
                    all_categories[category].extend(categorized[category])
       
        total_categorized_records = sum(len(category) for category in all_categories.values())
       
        # Store each category separately in Firestore
        results = {}
        for category, category_records in all_categories.items():
            if category_records:  # Only store if there are records in this category
                email_content = {
                    "recipient": request.recipient_email,
                    "subject": request.subject or f"{category.replace('_', ' ').title()} Expiry Alert - {request.doc} for {request.base} Base",
                    "category": category,
                    "expiry_period": {
                        "1_month": "0-30 days",
                        "3_month": "30-90 days",
                        "7_month": "90-210 days"
                    }[category],
                    "payload_data": {request.doc: category_records},
                    "summary": {
                        "total_records": len(category_records),
                        "document_type": request.doc,
                        "base": request.base,
                        "category": category,
                        "search_filter": request.search if request.search else "None",
                        "timestamp": datetime.now().isoformat()
                    },
                    "status": "pending",
                    "created_at": firestore.SERVER_TIMESTAMP
                }
               
                # Store with fixed category-specific document ID (no timestamp)
                document_id = f"{request.doc}_{category}"
                doc_ref = collection_ref.document(document_id)
                doc_ref.set(email_content)
                print(document_id)
                results[category] = {
                    "document_id": document_id,
                    "records_count": len(category_records)
                }
       
        logger.info(f"Categorized mail payload stored in Firestore for {request.recipient_email} with {total_categorized_records} categorized records from {total_original_records} original records")
       
        return JSONResponse({
            "success": True,
            "message": f"Categorized mail alerts stored successfully for {total_categorized_records} crew members.",
            "total_original_records": total_original_records,
            "total_categorized_records": total_categorized_records,
            "categories": results,
            "categorization_summary": {
                "1_month": f"{len(all_categories['1_month'])} records (0-30 days)",
                "3_month": f"{len(all_categories['3_month'])} records (30-90 days)",
                "7_month": f"{len(all_categories['7_month'])} records (90-210 days)"
            }
        })
       
    except Exception as e:
        logger.exception(f"Error storing categorized mail payload for {request.recipient_email}")
        return JSONResponse({
            "success": False,
            "error": "Failed to store categorized mail payload in Firestore",
            "message": str(e)
        }, status_code=500)
 
async def scheduled_mail_payload_api_call():
    """
    Scheduled function that makes HTTP calls to send-mail-payload-test endpoint.
    This hits the API endpoint for all document types (AEP, PVC, CMED, etc.) to update Firestore.
    """
    logger.info("[SCHEDULER] Starting scheduled API calls to send-mail-payload-test endpoint")
    
    try:
        # API endpoint URL (assuming running on localhost:8000)
        base_url = "http://localhost:8000"
        endpoint = "/api/send-mail-payload-test"
        
        # Document types to process
        document_types = ["AEP", "PVC", "CMED", "PASSPORT", "WMED"]
        
        # Base configuration
        base_config = {
            "search": "",
            "base": "DEL",
            "recipient_email": "scheduler@system.com",
            "subject": None  # Let the endpoint generate the subject
        }
        
        successful_requests = 0
        failed_requests = 0
        
        async with aiohttp.ClientSession() as session:
            for doc_type in document_types:
                try:
                    logger.info(f"[SCHEDULER] Making API call for {doc_type} documents")
                    
                    # Prepare request payload
                    payload = {
                        **base_config,
                        "doc": doc_type,
                        "subject": f"SCHEDULED Document Expiry Alert - {doc_type} for DEL Base"
                    }
                    
                    # Make HTTP POST request to the test endpoint
                    async with session.post(
                        f"{base_url}{endpoint}",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=300)  # 5 minute timeout
                    ) as response:
                        
                        if response.status == 200:
                            result = await response.json()
                            successful_requests += 1
                            
                            logger.info(f"[SCHEDULER] Successfully processed {doc_type}: "
                                      f"{result.get('total_categorized_records', 0)} records categorized")
                            
                            # Log categorization summary if available
                            if 'categorization_summary' in result:
                                summary = result['categorization_summary']
                                logger.info(f"[SCHEDULER] {doc_type} categorization: "
                                          f"1_month={summary.get('1_month', '0')}, "
                                          f"3_month={summary.get('3_month', '0')}, "
                                          f"7_month={summary.get('7_month', '0')}")
                        else:
                            failed_requests += 1
                            error_text = await response.text()
                            logger.error(f"[SCHEDULER] Failed to process {doc_type}: "
                                       f"Status {response.status}, Response: {error_text}")
                
                except asyncio.TimeoutError:
                    failed_requests += 1
                    logger.error(f"[SCHEDULER] Timeout error for {doc_type} documents")
                except Exception as e:
                    failed_requests += 1
                    logger.exception(f"[SCHEDULER] Error processing {doc_type}: {e}")
                
                # Small delay between requests to avoid overwhelming the server
                await asyncio.sleep(2)
        
        # Final summary
        total_requests = successful_requests + failed_requests
        logger.info(f"[SCHEDULER] Completed scheduled API calls: "
                   f"{successful_requests}/{total_requests} successful, "
                   f"{failed_requests} failed")
        
        if failed_requests > 0:
            logger.warning(f"[SCHEDULER] {failed_requests} document types failed to process")
        
    except Exception as e:
        logger.exception(f"[SCHEDULER] Critical error in scheduled API calls: {e}")
        raise

# Keep the original function for manual triggers
async def scheduled_mail_payload_update():
    """
    Original scheduled function - kept for backward compatibility and manual triggers.
    The scheduler now uses scheduled_mail_payload_api_call() instead.
    """
    logger.info("Starting scheduled mail payload update for PVC and AEP documents")
    
    try:
        # Create firestore client for mail service
        mail_service = get_firestore_client(os.getenv("FIRESTORE_DEFAULT_STORE"))
        collection_ref = mail_service.collection("mail_service_test")
        
        # Default parameters for scheduled run
        base = "DEL"
        search = ""
        recipient_email = "scheduler@system.com"
        
        # Process both PVC and AEP documents
        document_types = ["PVC", "AEP"]
        
        for doc_type in document_types:
            logger.info(f"Processing scheduled update for {doc_type} documents")
            
            # Get real data from the payload service
            payload_data = get_top_expiring(search, doc_type, base)
            
            if not payload_data or doc_type not in payload_data:
                logger.warning(f"No {doc_type} data found for scheduled update")
                continue
            
            # Get current date for comparison
            current_date = datetime.now()
            
            # Function to categorize records based on expiry date
            def categorize_by_expiry(records, doc_type):
                categories = {
                    "1_month": [],
                    "3_month": [],
                    "7_month": []
                }
                
                expiry_field = f"{doc_type.lower()}_expiry_date"
                logger.info(f"SCHEDULED: Categorizing {len(records)} records for doc_type: {doc_type}, looking for field: {expiry_field}")
                
                for record in records:
                    if expiry_field in record and record[expiry_field]:
                        try:
                            # Handle different date formats
                            expiry_str = record[expiry_field]
                            expiry_date = None
                            
                            if isinstance(expiry_str, str):
                                if 'T' in expiry_str:
                                    # Remove timezone info and parse
                                    clean_str = expiry_str.replace('Z', '').replace('+00:00', '')
                                    expiry_date = datetime.fromisoformat(clean_str)
                                else:
                                    expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d')
                            else:
                                # If it's already a datetime object
                                expiry_date = expiry_str
                            
                            if expiry_date:
                                days_until_expiry = (expiry_date - current_date).days
                                logger.debug(f"SCHEDULED Record {record.get('id', 'unknown')}: expiry={expiry_str}, days_until={days_until_expiry}")
                                
                                # Categorization logic
                                if days_until_expiry <= 30:  # This includes expired records (negative values)
                                    categories["1_month"].append(record)
                                elif days_until_expiry <= 90:
                                    categories["3_month"].append(record)
                                elif days_until_expiry <= 210:
                                    categories["7_month"].append(record)
                        
                        except (ValueError, TypeError) as e:
                            logger.warning(f"SCHEDULED: Invalid date format for record {record.get('id', 'unknown')}: {expiry_str}, error: {e}")
                            continue
                
                logger.info(f"SCHEDULED: Categorization results for {doc_type}: 1_month={len(categories['1_month'])}, 3_month={len(categories['3_month'])}, 7_month={len(categories['7_month'])}")
                return categories
            
            # Process and categorize data
            all_categories = {"1_month": [], "3_month": [], "7_month": []}
            total_original_records = 0
            
            for doc_name, records in payload_data.items():
                if records:
                    total_original_records += len(records)
                    categorized = categorize_by_expiry(records, doc_name)
                    for category in all_categories:
                        all_categories[category].extend(categorized[category])
            
            total_categorized_records = sum(len(category) for category in all_categories.values())
            
            # Store each category separately in Firestore
            for category, category_records in all_categories.items():
                if category_records:  # Only store if there are records in this category
                    email_content = {
                        "recipient": recipient_email,
                        "subject": f"SCHEDULED {category.replace('_', ' ').title()} Expiry Alert - {doc_type} for {base} Base",
                        "category": category,
                        "expiry_period": {
                            "1_month": "0-30 days",
                            "3_month": "30-90 days",
                            "7_month": "90-210 days"
                        }[category],
                        "payload_data": {doc_type: category_records},
                        "summary": {
                            "total_records": len(category_records),
                            "document_type": doc_type,
                            "base": base,
                            "category": category,
                            "search_filter": search if search else "None",
                            "timestamp": datetime.now().isoformat(),
                            "scheduled_update": True
                        },
                        "status": "scheduled",
                        "created_at": firestore.SERVER_TIMESTAMP,
                        "last_updated": firestore.SERVER_TIMESTAMP
                    }
                    
                    # Store with fixed category-specific document ID
                    document_id = f"SCHEDULED_{doc_type}_{category}"
                    doc_ref = collection_ref.document(document_id)
                    doc_ref.set(email_content)
                    
                    logger.info(f"SCHEDULED: Updated {document_id} with {len(category_records)} records")
            
            logger.info(f"SCHEDULED: Completed update for {doc_type} with {total_categorized_records} categorized records from {total_original_records} original records")
        
        logger.info("Scheduled mail payload update completed successfully for all document types")
        
    except Exception as e:
        logger.exception(f"Error in scheduled mail payload update: {e}")
        raise

@app.post("/api/send-mail-payload-test")
async def send_mail_payload_test(request: SendMailPayloadRequest):
    """
    API endpoint to store categorized mail payload data in Firestore based on expiry periods - USES HARDCODED TEST DATA.
    This endpoint uses hardcoded test data with 5 records each for PVC and AEP documents for testing purposes.
    
    Categories:
    - 1_month: Documents expiring within 30 days
    - 3_month: Documents expiring between 30-90 days  
    - 7_month: Documents expiring between 90-210 days
   
    Parameters:
    - search: Search term for filtering records (optional)
    - doc: Document type (PVC, AEP, or 'ALL' for both)
    - base: Base location (default: DEL)
    - recipient_email: Email address to send the payload to
    - subject: Email subject (optional)
    """
    try:
        logger.info(f"Storing categorized mail payload with HARDCODED TEST DATA for {request.recipient_email}")
       
        # Create firestore client for mail service (using test collection)
        mail_service = get_firestore_client(os.getenv("FIRESTORE_DEFAULT_STORE"))
        collection_ref = mail_service.collection("mail_service_test")
       
        # Determine which document types to process
        if request.doc.upper() == "ALL":
            doc_types_to_process = ["PVC", "AEP", "PASSPORT", "CMC", "CMED"]
        else:
            doc_types_to_process = [request.doc.upper()]
        
        # Hardcoded test data strategically distributed across 3 categories (1_month, 3_month, 7_month)
        # Current date reference: November 27, 2025
        hardcoded_test_data = {
            "PVC": [
                # 1_month category (≤ 30 days) - expires soon
                {
                    "id": "IGA34782",
                    "name": "Shubham, Buara",
                    "email": "shubham.x.buara@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "pvc_expiry_date": "2025-12-15T00:00:00Z"  # ~18 days from now
                },
                {
                    "id": "IGA45123", 
                    "name": "Akshat, Jain",
                    "email": "akshat.x.jain@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "pvc_expiry_date": "2025-12-25T00:00:00Z"  # ~28 days from now
                },
                # 3_month category (31-90 days)
                {
                    "id": "IGA67891",
                    "name": "jatin, agarwal",
                    "email": "jatin.x.agarwal@goindigo.in",
                    "phone": "9999999999", 
                    "base": "DEL",
                    "pvc_expiry_date": "2026-01-15T00:00:00Z"  # ~49 days from now
                },
                {
                    "id": "IGA78456",
                    "name": "Gaurav, Tripathi",
                    "email": "gaurav.x.tripathi@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL", 
                    "pvc_expiry_date": "2026-02-10T00:00:00Z"  # ~75 days from now
                },
                # 7_month category (91-210 days)
                {
                    "id": "IGA89234",
                    "name": "Mohit, Chaudhary",
                    "email": "mohit.x.chaudhary@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "pvc_expiry_date": "2026-04-15T00:00:00Z"  # ~139 days from now
                }
            ],
            "AEP": [
                # 1_month category (≤ 30 days) - expires soon  
                {
                    "id": "IGA67890",
                    "name": "Tanuj, pant",
                    "email": "tanuj.pant@GOINDIGO.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "aep_expiry_date": "2025-12-10T00:00:00Z"  # ~13 days from now
                },
                {
                    "id": "IGA56789",
                    "name": "Aman, Yadav",
                    "email": "aman.yadav5@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "aep_expiry_date": "2025-12-20T00:00:00Z"  # ~23 days from now
                },
                # 3_month category (31-90 days)
                {
                    "id": "IGA12345",
                    "name": "Ankit, yadav",
                    "email": "ankit",
                    "phone": "9999999999",
                    "base": "DEL",
                    "aep_expiry_date": "2026-01-25T00:00:00Z"  # ~59 days from now
                },
                # 7_month category (91-210 days)
                {
                    "id": "IGA98765",
                    "name": "Aditya, Singh",
                    "email": "aditya.singh19@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "aep_expiry_date": "2026-03-15T00:00:00Z"  # ~108 days from now
                },
                {
                    "id": "IGA11223",
                    "name": "Simran, Chibber",
                    "email": "simran.x.chibber@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "aep_expiry_date": "2026-05-20T00:00:00Z"  # ~174 days from now
                }
            ],
            "PASSPORT": [
                # 1_month category (≤ 30 days) - expires soon
                {
                    "id": "IGA22334",
                    "name": "Shubham, Singh",
                    "email": "shubham.x.buara@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "passport_expiry_date": "2025-12-12T00:00:00Z"  # ~15 days from now
                },
                {
                    "id": "IGA33445",
                    "name": "Kishan, Tiwari",
                    "email": "kishan.x.tiwari@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "passport_expiry_date": "2025-12-28T00:00:00Z"  # ~26 days from now
                },
                # 3_month category (31-90 days)
                {
                    "id": "IGA44556",
                    "name": "Ankit, yadav",
                    "email": "ankit.yadav11@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "passport_expiry_date": "2026-01-20T00:00:00Z"  # ~54 days from now
                },
                {
                    "id": "IGA55667",
                    "name": "akshat, Jain",
                    "email": "akshat.x.jain@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "passport_expiry_date": "2026-02-15T00:00:00Z"  # ~80 days from now
                },
                # 7_month category (91-210 days)
                {
                    "id": "IGA66778",
                    "name": "jatin, agarwal",
                    "email": "jatin.x.agarwal@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "passport_expiry_date": "2026-04-20T00:00:00Z"  # ~144 days from now
                }
            ],
            "CMC": [
                # 1_month category (≤ 30 days) - expires soon
                {
                    "id": "IGA77889",
                    "name": "Tanuj, pant",
                    "email": "tanuj.pant@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmc_expiry_date": "2025-12-18T00:00:00Z"  # ~21 days from now
                },
                {
                    "id": "IGA88990",
                    "name": "Aman, yadav",
                    "email": "aman.yadav5@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmc_expiry_date": "2025-12-30T00:00:00Z"  # ~28 days from now
                },
                # 3_month category (31-90 days)
                {
                    "id": "IGA99001",
                    "name": "Mohit, Chaudhary",
                    "email": "mohit.x.chaudhary@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmc_expiry_date": "2026-01-30T00:00:00Z"  # ~64 days from now
                },
                {
                    "id": "IGA10112",
                    "name": "Gaurav, tripathi",
                    "email": "gaurav.x.tripathi@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmc_expiry_date": "2026-02-20T00:00:00Z"  # ~85 days from now
                },
                # 7_month category (91-210 days)
                {
                    "id": "IGA21223",
                    "name": "viraj, raina",
                    "email": "viraj.x.raina@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmc_expiry_date": "2026-04-25T00:00:00Z"  # ~149 days from now
                }
            ],
            "CMED": [
                # 1_month category (≤ 30 days) - expires soon
                {
                    "id": "IGA32334",
                    "name": "shuhbham, singh",
                    "email": "shuhbham.x.buara@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmed_expiry_date": "2025-12-14T00:00:00Z"  # ~17 days from now
                },
                {
                    "id": "IGA43445",
                    "name": "ankit, yadav",
                    "email": "ankit.yadav11@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmed_expiry_date": "2025-12-27T00:00:00Z"  # ~25 days from now
                },
                # 3_month category (31-90 days)
                {
                    "id": "IGA54556",
                    "name": "aman, yadav",
                    "email": "aman.yadav5@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmed_expiry_date": "2026-01-28T00:00:00Z"  # ~62 days from now
                },
                {
                    "id": "IGA65667",
                    "name": "akshat, jain",
                    "email": "akshat.x.jain@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmed_expiry_date": "2026-02-18T00:00:00Z"  # ~83 days from now
                },
                # 7_month category (91-210 days)
                {
                    "id": "IGA76778",
                    "name": "jatin, agarwal",
                    "email": "jatin.x.agarwal@goindigo.in",
                    "phone": "9999999999",
                    "base": "DEL",
                    "cmed_expiry_date": "2026-04-30T00:00:00Z"  # ~154 days from now
                }
            ]
        }
        
        # Use hardcoded test data instead of real data
        all_payload_data = {}
        for doc_type in doc_types_to_process:
            if doc_type in hardcoded_test_data:
                all_payload_data[doc_type] = hardcoded_test_data[doc_type]
                logger.info(f"Using hardcoded test data for {doc_type}: {len(hardcoded_test_data[doc_type])} records")
            else:
                all_payload_data[doc_type] = []
                logger.warning(f"No hardcoded test data found for {doc_type} document type")
       
        # Get current date for comparison (SAME LOGIC AS PRODUCTION)
        current_date = datetime.now()
       
        # Function to categorize records based on expiry date (USING HARDCODED TEST DATA)
        def categorize_by_expiry(records, doc_type):
            categories = {
                "1_month": [],
                "3_month": [],
                "7_month": []
            }
           
            expiry_field = f"{doc_type.lower()}_expiry_date"
            logger.info(f"TEST: Categorizing {len(records)} records for doc_type: {doc_type}, looking for field: {expiry_field}")
           
            for record in records:
                if expiry_field in record and record[expiry_field]:
                    try:
                        # Handle different date formats (SAME AS PRODUCTION)
                        expiry_str = record[expiry_field]
                        expiry_date = None
                       
                        if isinstance(expiry_str, str):
                            if 'T' in expiry_str:
                                # Remove timezone info and parse
                                clean_str = expiry_str.replace('Z', '').replace('+00:00', '')
                                expiry_date = datetime.fromisoformat(clean_str)
                            else:
                                expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d')
                        else:
                            # If it's already a datetime object
                            expiry_date = expiry_str
                       
                        if expiry_date:
                            days_until_expiry = (expiry_date - current_date).days
                            logger.debug(f"TEST Record {record.get('id', 'unknown')}: expiry={expiry_str}, days_until={days_until_expiry}")
                           
                            # IDENTICAL CATEGORIZATION LOGIC AS PRODUCTION
                            if days_until_expiry <= 30:  # This includes expired records (negative values)
                                categories["1_month"].append(record)
                                logger.debug(f"TEST: Added to 1_month: {record.get('id', 'unknown')} (days: {days_until_expiry})")
                            elif days_until_expiry <= 90:
                                categories["3_month"].append(record)
                                logger.debug(f"TEST: Added to 3_month: {record.get('id', 'unknown')} (days: {days_until_expiry})")
                            elif days_until_expiry <= 210:
                                categories["7_month"].append(record)
                                logger.debug(f"TEST: Added to 7_month: {record.get('id', 'unknown')} (days: {days_until_expiry})")
                            else:
                                logger.debug(f"TEST: Record {record.get('id', 'unknown')} expires in {days_until_expiry} days, beyond 7 months")
                       
                    except (ValueError, TypeError) as e:
                        logger.warning(f"TEST: Invalid date format for record {record.get('id', 'unknown')}: {expiry_str}, error: {e}")
                        continue
                else:
                    logger.debug(f"TEST: Record {record.get('id', 'unknown')} missing or null {expiry_field}")
           
            logger.info(f"TEST: Categorization results: 1_month={len(categories['1_month'])}, 3_month={len(categories['3_month'])}, 7_month={len(categories['7_month'])}")
            return categories
       
        # Process and categorize data for each document type (SAME LOGIC AS PRODUCTION)
        all_categories = {"1_month": [], "3_month": [], "7_month": []}
        total_original_records = 0
       
        for doc_type, records in all_payload_data.items():
            if records:
                total_original_records += len(records)
                categorized = categorize_by_expiry(records, doc_type)
                for category in all_categories:
                    all_categories[category].extend(categorized[category])
       
        total_categorized_records = sum(len(category) for category in all_categories.values())
       
        # Store each category separately in Firestore (IDENTICAL TO PRODUCTION)
        results = {}
        processed_doc_types = [doc for doc in all_payload_data.keys() if all_payload_data[doc]]
        
        for category, category_records in all_categories.items():
            if category_records:  # Only store if there are records in this category
                # Create payload data with all processed document types
                payload_for_category = {}
                for doc_type in processed_doc_types:
                    doc_records = [r for r in category_records if f"{doc_type.lower()}_expiry_date" in r]
                    if doc_records:
                        payload_for_category[doc_type] = doc_records
                
                email_content = {
                    "recipient": request.recipient_email,
                    "subject": request.subject or f"TEST {category.replace('_', ' ').title()} Expiry Alert - {'/'.join(processed_doc_types)} for {request.base} Base",
                    "category": category,
                    "expiry_period": {
                        "1_month": "0-30 days",
                        "3_month": "30-90 days",
                        "7_month": "90-210 days"
                    }[category],
                    "payload_data": payload_for_category,
                    "summary": {
                        "total_records": len(category_records),
                        "document_types": processed_doc_types,
                        "base": request.base,
                        "category": category,
                        "search_filter": request.search if request.search else "None",
                        "timestamp": datetime.now().isoformat(),
                        "real_data": True
                    },
                    "status": "pending",
                    "created_at": firestore.SERVER_TIMESTAMP
                }
               
                # Store with fixed category-specific document ID (SAME PATTERN AS PRODUCTION)
                document_id = f"TEST_{'_'.join(processed_doc_types)}_{category}"
                doc_ref = collection_ref.document(document_id)
                doc_ref.set(email_content)
                
                results[category] = {
                    "document_id": document_id,
                    "records_count": len(category_records)
                }
       
        logger.info(f"TEST: Categorized mail payload stored in Firestore for {request.recipient_email} with {total_categorized_records} categorized records from {total_original_records} original records")
       
        return JSONResponse({
            "success": True,
            "message": f"TEST: Categorized mail alerts stored successfully for {total_categorized_records} crew members from {'/'.join(processed_doc_types)} documents.",
            "total_original_records": total_original_records,
            "total_categorized_records": total_categorized_records,
            "processed_document_types": processed_doc_types,
            "categories": results,
            "categorization_summary": {
                "1_month": f"{len(all_categories['1_month'])} records (0-30 days)",
                "3_month": f"{len(all_categories['3_month'])} records (30-90 days)",
                "7_month": f"{len(all_categories['7_month'])} records (90-210 days)"
            },
            "real_data": True
        })
       
    except Exception as e:
        logger.exception(f"Error storing TEST categorized mail payload for {request.recipient_email}")
        return JSONResponse({
            "success": False,
            "error": "Failed to store TEST categorized mail payload in Firestore",
            "message": str(e)
        }, status_code=500)

@app.post("/api/trigger-scheduled-mail-update")
async def trigger_scheduled_mail_update():
    """
    Manual trigger for the scheduled mail payload update - for testing purposes.
    This endpoint manually runs the same API calls that the scheduler executes.
    """
    try:
        logger.info("[MANUAL] Manual trigger for scheduled mail payload API calls")
        await scheduled_mail_payload_api_call()
        return JSONResponse({
            "success": True,
            "message": "Scheduled mail payload API calls completed successfully",
            "trigger": "manual",
            "method": "api_calls"
        })
    except Exception as e:
        logger.exception("Error in manual scheduled mail payload API calls trigger")
        return JSONResponse({
            "success": False,
            "error": "Failed to complete scheduled mail payload API calls",
            "message": str(e)
        }, status_code=500)

@app.post("/api/trigger-scheduled-mail-update-direct")
async def trigger_scheduled_mail_update_direct():
    """
    Manual trigger for the direct scheduled mail payload update (old method).
    This endpoint manually runs the direct function without API calls.
    """
    try:
        logger.info("[MANUAL] Manual trigger for direct scheduled mail payload update")
        await scheduled_mail_payload_update()
        return JSONResponse({
            "success": True,
            "message": "Direct scheduled mail payload update completed successfully",
            "trigger": "manual",
            "method": "direct"
        })
    except Exception as e:
        logger.exception("Error in manual direct scheduled mail payload update trigger")
        return JSONResponse({
            "success": False,
            "error": "Failed to complete direct scheduled mail payload update",
            "message": str(e)
        }, status_code=500)

@app.post("/api/upload-leave-excel")
async def upload_leave_excel(request: Request, file: UploadFile = File(...)):
    headers = request.headers
    user_base = headers.get("x-user-base")  
    authorization = headers.get("authorization")  
    decoded_token = None
    if authorization:
        try:
            if authorization.startswith("Bearer "):
                token = authorization[7:]
            else:
                token = authorization
            decoded_token = jwt.decode(token, options={"verify_signature": False})
            logger.info(f"Decoded JWT: {decoded_token}")
            
            # Extract user_base from nested info object
            token_user_base = None
            token_iga_code = None
            token_user_name = None
            
            if "info" in decoded_token and isinstance(decoded_token["info"], dict):
                token_user_base = decoded_token["info"].get("user_base")
                token_iga_code = decoded_token["info"].get("iga_code")
 
            # token_user_base= 'DEL'
            # Fallback to top-level user_base
            if not token_user_base:
                token_user_base = decoded_token.get("user_base")
            if not token_iga_code:
                token_iga_code = decoded_token.get("iga_code")
            if not token_user_name:
                token_user_name = decoded_token.get("name")    
            
            if token_user_base:
                user_base = token_user_base.strip().upper()
                user_base = "DEL" if user_base in ("CORP", "ISC") else user_base
                
                logger.info(f"Extracted user_base from JWT: {user_base}")
            
            print(f"Token user_base: {token_user_base}, Final user_base: {user_base}")
        except Exception as e:
            print(type(decoded_token),"--------------------------------------------")
            logger.error(f"Failed to decode JWT token: {e}")
            print(f"Failed to decode JWT token: {e}")

        
    logger.info(f"Excel upload requested by user from base: {user_base}")
    
    # Set logging level to capture all debug messages during Excel processing
    import logging
    logging.getLogger().setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)
    
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only Excel files are supported.")
 
    contents = await file.read()
    try:
        # Read Excel without any date conversion - let pandas read dates as datetime objects
        # then we'll handle the conversion in validate_date function
        df = pd.read_excel(
            BytesIO(contents), 
            keep_default_na=False
        )
        
        # Convert NaN and empty values back to proper format for processing
        df = df.fillna('')
        
        # Debug: Log what pandas actually read from Excel
        logger.info(f"Excel DataFrame shape: {df.shape}")
        logger.info(f"Column names: {list(df.columns)}")
        if not df.empty:
            logger.info(f"Raw Excel data (first 5 rows):")
            for idx, row in df.head(5).iterrows():
                logger.info(f"Row {idx + 2}: {dict(row)}")
            
            # Specifically log date columns to debug date parsing issues
            if 'From Date' in df.columns and 'To Date' in df.columns:
                logger.info(f"Date column samples:")
                logger.info(f"From Date values: {df['From Date'].head(5).tolist()}")
                logger.info(f"To Date values: {df['To Date'].head(5).tolist()}")
                logger.info(f"From Date types: {[type(x) for x in df['From Date'].head(5)]}")
                logger.info(f"To Date types: {[type(x) for x in df['To Date'].head(5)]}")
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read Excel: {e}")
 
    # Check for empty dataframe
    if df.empty:
        raise HTTPException(status_code=400, detail="Excel file is empty. Please provide data.")
    
    # Make column names case-insensitive by creating a mapping
    df.columns = df.columns.str.strip()  # Remove any leading/trailing spaces
    
    
    column_mapping = {}
    
    # Create case-insensitive column mapping for expected headers
    expected_headers = {
        'iga': ['iga code', 'iga_code', 'igacode', 'iga'],
        'name': ['name', 'employee name', 'employee_name'],
        'from_date': ['from date', 'from_date', 'start date', 'start_date', 'fromdate'],
        'to_date': ['to date', 'to_date', 'end date', 'end_date', 'todate']
    }
    
    # Map only the recognized columns, ignore extra columns
    for col in df.columns:
        col_lower = col.lower().replace(" ", "").replace("_", "")
        matched = False
        
        for key, variations in expected_headers.items():
            for variation in variations:
                variation_clean = variation.lower().replace(" ", "").replace("_", "")
                if col_lower == variation_clean:
                    column_mapping[key] = col
                    matched = True
                    break
            if matched:
                break
        # If not matched, simply ignore the extra column (no error thrown)
    
    # Check if required columns are found
    required_mappings = ['iga', 'from_date', 'to_date']
    missing_columns = []
    for req_col in required_mappings:
        if req_col not in column_mapping:
            missing_columns.append(req_col)
    
    if missing_columns:
        missing_display = []
        for missing in missing_columns:
            if missing == 'iga':
                missing_display.append('IGA Code')
            elif missing == 'from_date':
                missing_display.append('From Date')
            elif missing == 'to_date':
                missing_display.append('To Date')
        
        raise HTTPException(
            status_code=400, 
            detail=f"Missing required columns: {missing_display}. Required headers: 'IGA Code', 'Name', 'From Date', 'To Date'"
        )
 
    def validate_iga_code(iga_value):
        """Validate and extract IGA code"""
        if pd.isna(iga_value) or str(iga_value).strip() == '':
            return None, "IGA code is empty"
        
        iga_str = str(iga_value).strip()
        
        # Check for invalid characters like spaces, commas, special characters
        if re.search(r'[,\s]', iga_str):
            return None, f"IGA code contains invalid characters'{iga_str}'"
        
        # Extract numeric part
        if iga_str.upper().startswith('IGA'):
            numeric_part = iga_str[3:].strip()
        else:
            numeric_part = iga_str
        
        # Handle float conversion from Excel (e.g., 38721.0)
        if numeric_part.endswith('.0'):
            numeric_part = numeric_part[:-2]  # Remove .0 suffix
        
        # Validate that it's numeric and reasonable length
        if not numeric_part.isdigit():
            return None, f"IGA code must be numeric: '{iga_str}'"
        
        if len(numeric_part) < 3 or len(numeric_part) > 6:
            return None, f"IGA code must be 3-6 digits: '{iga_str}'"
        
        return numeric_part, None

    def format_date_for_display(date_obj):
        """Format date object consistently as DD/MM/YYYY for error messages"""
        if hasattr(date_obj, 'strftime'):
            return date_obj.strftime('%d/%m/%Y')
        return str(date_obj)

    def validate_date(date_value, date_type):
        """Validate date values - ALWAYS interpret as DD/MM/YYYY format"""
        if pd.isna(date_value) or str(date_value).strip() == '':
            return None, f"{date_type} is empty"
        
        # Check for obviously invalid date strings first
        initial_date_str = str(date_value).strip()
        if initial_date_str in ['00/00/0000', '0/0/0', 'nan', 'NaN', 'null', 'NULL']:
            return None, f"Invalid {date_type} format: '{initial_date_str}'. Please use DD/MM/YYYY or DD-MM-YYYY format"
        
        # Handle pandas datetime objects from Excel
        if isinstance(date_value, (pd.Timestamp, datetime)):
            logger.info(f"[DATE_PARSE] Got datetime object for {date_type}: {date_value}")
            # Pandas datetime is WRONG because it interpreted DD/MM/YYYY as MM/DD/YYYY
            # We need to convert it to string and then reparse with correct DD/MM/YYYY logic
            date_str = date_value.strftime('%Y-%m-%d')
            logger.warning(f"[DATE_PARSE] Converting pandas datetime to string for reprocessing: '{date_str}'")
            # Continue to string processing logic below
        
        # Always convert to string and clean it
        date_str = str(date_value).strip()
        logger.info(f"[DATE_PARSE] Raw string input for {date_type}: '{date_str}' (type: {type(date_value)})")
        
        # Handle different date string formats
        
        # Case 1: YYYY-MM-DD format (from pandas misinterpretation) - need to swap MM/DD back to DD/MM
        if re.match(r'^\d{4}-\d{1,2}-\d{1,2}', date_str):
            # Remove time portion if present
            if ' ' in date_str:
                date_str = date_str.split(' ')[0]
            
            parts = date_str.split('-')
            year, pandas_month, pandas_day = parts[0], parts[1], parts[2]
            
            # Pandas interpreted DD/MM/YYYY as MM/DD/YYYY, so we need to swap back
            # Original: 2/10/2015 (day=2, month=10)  
            # Pandas saw: 2/10/2015 -> thought month=2, day=10 -> 2015-02-10
            # So: pandas_month=02 is actually original day, pandas_day=10 is actually original month
            actual_day = pandas_month   
            actual_month = pandas_day    
            
            logger.warning(f"[DATE_PARSE] YYYY-MM-DD from pandas detected: '{date_str}'")
            logger.info(f"[DATE_PARSE] Original pandas interpretation: year={year}, pandas_month={pandas_month}, pandas_day={pandas_day}")
            logger.info(f"[DATE_PARSE] Swapping back to DD/MM/YYYY: actual_day={actual_day}, actual_month={actual_month}, year={year}")
            logger.info(f"[DATE_PARSE] This means: {actual_day}th day of month {actual_month} = {actual_day}/{actual_month}/{year}")
            
            try:
                y, m, d = int(year), int(actual_month), int(actual_day)
                
                # Validate the corrected values
                if d < 1 or d > 31:
                    return None, f"Invalid day in {date_type}: '{d}'"
                if m < 1 or m > 12:
                    return None, f"Invalid month in {date_type}: '{m}'"
                
                parsed_date = datetime(y, m, d)
                
                # Additional validation for reasonable date range
                current_date = datetime.now().date()
                min_date = datetime(2010, 1, 1).date()
                
                if parsed_date.date() < min_date:
                    return None, f"Invalid {date_type}: date '{parsed_date.strftime('%d/%m/%Y')}' is too old (before 2010)"
                
                if parsed_date.date() > current_date:
                    return None, f"Invalid {date_type}: future dates not allowed. '{parsed_date.strftime('%d/%m/%Y')}' is after today ({current_date.strftime('%d/%m/%Y')})"
                
                result_display = parsed_date.strftime('%d/%m/%Y')
                logger.info(f"[DATE_PARSE] CORRECTED SUCCESS: Original='{date_value}' -> Final='{result_display}' (DD/MM/YYYY)")
                return parsed_date, None
            except ValueError as ve:
                return None, f"Invalid date in {date_type}: '{date_str}' - {str(ve)}"
        
        # Case 2: String dates - strict validation for DD/MM/YYYY or DD-MM-YYYY only
        
        # Check for alphabets and special characters in date (reject dots, spaces, commas etc.)
        if re.search(r'[a-zA-Z!@#$%^&*()_+=\[\]{}|;:,.<>?~`\s]', date_str):
            return None, f"Invalid {date_type} format: '{date_str}'. Please use DD/MM/YYYY or DD-MM-YYYY format only"
        
        # Only allow / and - as date separators (reject dots, spaces, etc.)
        if not re.match(r'^\d{1,2}[/-]\d{1,2}[/-]\d{4}$', date_str):
            return None, f"Invalid {date_type} format: '{date_str}'. Please use DD/MM/YYYY or DD-MM-YYYY format only"
        
        # Normalize separators
        date_str = date_str.replace('-', '/')
        logger.info(f"[DATE_PARSE] Normalized separators: '{date_str}'")
        
        try:
            parts = date_str.split('/')
            if len(parts) != 3:
                return None, f"Invalid {date_type} format: '{date_value}'. Expected DD/MM/YYYY format"
            
            # ALWAYS interpret as DD/MM/YYYY regardless of input
            day_str, month_str, year_str = parts[0], parts[1], parts[2]
            logger.info(f"[DATE_PARSE] Parsing '{date_str}' as DD/MM/YYYY -> day='{day_str}', month='{month_str}', year='{year_str}'")
            
            try:
                day = int(day_str)
                month = int(month_str)
                year = int(year_str)
            except ValueError:
                return None, f"Invalid {date_type}: non-numeric values in '{date_value}'"
            
            # Validate ranges for DD/MM/YYYY interpretation
            if day < 1 or day > 31:
                return None, f"Invalid day in {date_type}: '{day}' from '{date_value}'. Day must be 1-31 in DD/MM/YYYY format"
            if month < 1 or month > 12:
                return None, f"Invalid month in {date_type}: '{month}' from '{date_value}'. Month must be 1-12 in DD/MM/YYYY format"
            if year < 1900 or year > 2100:
                return None, f"Invalid year in {date_type}: '{year}' from '{date_value}'. Year must be 1900-2100"
            
            # Create datetime object with DD/MM/YYYY interpretation
            try:
                parsed_date = datetime(year, month, day)
                
                # Additional validation for reasonable date range (from original code)
                current_date = datetime.now().date()
                min_date = datetime(2010, 1, 1).date()
                
                if parsed_date.date() < min_date:
                    return None, f"Invalid {date_type}: date '{parsed_date.strftime('%d/%m/%Y')}' is too old (before 2010)"
                
                # Check for future dates - both From Date and To Date should not be in future
                if parsed_date.date() > current_date:
                    return None, f"Invalid {date_type}: future dates not allowed. '{parsed_date.strftime('%d/%m/%Y')}' is after today ({current_date.strftime('%d/%m/%Y')})"
                
                logger.info(f"[DATE_PARSE] SUCCESS: '{date_value}' -> {parsed_date.strftime('%d/%m/%Y')} (DD/MM/YYYY)")
                return parsed_date, None
                
            except ValueError as ve:
                return None, f"Invalid date in {date_type}: '{date_value}' -> day={day}, month={month}, year={year} - {str(ve)}"
                
        except Exception as e:
            logger.error(f"[DATE_PARSE] Error parsing '{date_value}': {e}")
            return None, f"Error parsing {date_type}: '{date_value}'. Expected DD/MM/YYYY format"
 
    # Check for duplicate IGA codes and overlapping date ranges to prevent duplicate entries
    def check_for_duplicates_and_overlaps():
        """Check for duplicate IGA codes with overlapping date ranges in the Excel file and database"""
        duplicate_errors = []
        processed_records = {}
        
        for idx, row in df.iterrows():
            row_num = idx + 2
            iga_code, _ = validate_iga_code(row[column_mapping['iga']])
            if not iga_code:
                continue
                
            from_date, from_error = validate_date(row[column_mapping['from_date']], "From Date")
            to_date, to_error = validate_date(row[column_mapping['to_date']], "To Date")
            
            # Debug: Log the parsed dates for this row
            logger.info(f"[DUPLICATE_CHECK] Row {row_num}: Raw dates - From: '{row[column_mapping['from_date']]}', To: '{row[column_mapping['to_date']]}'")
            logger.info(f"[DUPLICATE_CHECK] Row {row_num}: Parsed dates - From: {from_date}, To: {to_date}")
            if from_error:
                logger.info(f"[DUPLICATE_CHECK] Row {row_num}: From date error: {from_error}")
            if to_error:
                logger.info(f"[DUPLICATE_CHECK] Row {row_num}: To date error: {to_error}")
            
            if not (from_date and to_date):
                continue
            
            # Check within Excel file for duplicates with correct logic
            if iga_code in processed_records:
                for existing_record in processed_records[iga_code]:
                    existing_from, existing_to, existing_row = existing_record
                    # Enhanced debug logging for overlap detection
                    from_display = from_date.strftime('%d/%m/%Y') if from_date else 'Invalid'
                    to_display = to_date.strftime('%d/%m/%Y') if to_date else 'Invalid'
                    existing_from_display = existing_from.strftime('%d/%m/%Y') if existing_from else 'Invalid'
                    existing_to_display = existing_to.strftime('%d/%m/%Y') if existing_to else 'Invalid'
                    logger.info(f"[OVERLAP_CHECK] Row {row_num} ({from_display} to {to_display}) vs Row {existing_row} ({existing_from_display} to {existing_to_display})")
                    logger.info(f"[OVERLAP_CHECK] Dates as objects: {from_date} to {to_date} vs {existing_from} to {existing_to}")
                    
                    # Check for overlapping dates: dates overlap if they share any common day
                    # Two date ranges [A,B] and [C,D] overlap if: A <= D AND C <= B
                    overlap_condition1 = from_date <= existing_to
                    overlap_condition2 = existing_from <= to_date
                    logger.info(f"[OVERLAP_CHECK] Condition1 ({from_date} <= {existing_to}): {overlap_condition1}")
                    logger.info(f"[OVERLAP_CHECK] Condition2 ({existing_from} <= {to_date}): {overlap_condition2}")
                    
                    if overlap_condition1 and overlap_condition2:
                        duplicate_errors.append(f"Row {row_num}: Duplicate leave found with overlapping dates for IGA {iga_code} (conflicts with Row {existing_row})")
                        logger.info(f"[OVERLAP_CHECK] OVERLAP DETECTED: Row {row_num} conflicts with Row {existing_row}")
                        break
                    else:
                        logger.info(f"[OVERLAP_CHECK] NO OVERLAP: Row {row_num} does not conflict with Row {existing_row}")
            
            # Check database for existing overlapping leaves
            try:
                sql = f"""
                SELECT start_date, end_date 
                FROM `{table_ref()}` 
                WHERE iga_code = @iga_code 
                AND status IN ('pending', 'approved')
                AND (
                    DATE(@start_date) <= DATE(end_date) AND 
                    DATE(@end_date) >= DATE(start_date)
                )
                """
                existing_leaves = query_rows(sql, {
                    "iga_code": iga_code,
                    "start_date": from_date.strftime('%Y-%m-%d'),
                    "end_date": to_date.strftime('%Y-%m-%d')
                })
                
                if existing_leaves:
                    from_display = from_date.strftime('%d/%m/%Y') if from_date else 'Invalid'
                    to_display = to_date.strftime('%d/%m/%Y') if to_date else 'Invalid'
                    duplicate_errors.append(f"Row {row_num}: Leave for IGA {iga_code} ({from_display} to {to_display}) overlaps with existing leave in database")
            except Exception:
                # If database check fails, continue with processing
                pass
            
            # Track processed records in this Excel
            if iga_code not in processed_records:
                processed_records[iga_code] = []
            processed_records[iga_code].append((from_date, to_date, row_num))
        
        return duplicate_errors
 
    # Check for duplicates first
    duplicate_errors = check_for_duplicates_and_overlaps()
    
    # Process each row individually - continue with valid rows even if some have errors
    results = []
    success_count = 0
    error_count = 0
    validation_errors = []
    
    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row number
        row_errors = []
        
        try:
            # First validate IGA code to get it for base authorization check
            raw_iga_code = str(row[column_mapping['iga']]).strip()
            iga_code, iga_error = validate_iga_code(raw_iga_code)
            
            # PRIORITY 1: Base authorization check (security first)
            if iga_code:  # Only check if IGA code is valid format
                try:
                    crew_info = await fetch_crew_info(client=client, iga=iga_code)
                    if not crew_info or not isinstance(crew_info, dict) or 'data' not in crew_info:
                        row_errors.append(f"IGA code {iga_code} does not exist or is not valid")
                    elif not crew_info.get('data') or not crew_info['data'].get('name'):
                        row_errors.append(f"IGA code {iga_code} does not exist or is not valid")
                    else:
                        # Base authorization check - crew must belong to same base as user
                        crew_base = crew_info['data'].get('base', '').strip().upper()
                        if not crew_base:
                            row_errors.append(f"IGA code {iga_code}: crew base information not available")
                        elif crew_base != user_base:
                            row_errors.append(f"Unauthorized: IGA {iga_code} belongs to '{crew_base}' base, but you can only upload leaves for '{user_base}' base crew members")
                except Exception as crew_error:
                    row_errors.append(f"IGA code {iga_code} could not be validated (API error)")
            
            # PRIORITY 2: Other validation errors (only if no base authorization errors)
            if not row_errors:
                # Validate IGA code format
                if iga_error:
                    row_errors.append(iga_error)
                
                # Validate dates for this row
                from_date, from_error = validate_date(row[column_mapping['from_date']], "From Date")
                if from_error:
                    row_errors.append(from_error)
                
                to_date, to_error = validate_date(row[column_mapping['to_date']], "To Date")
                if to_error:
                    row_errors.append(to_error)
                
                # Validate date logic for this row
                if from_date and to_date and from_date > to_date:
                    from_display = from_date.strftime('%d/%m/%Y') if from_date else 'Invalid'
                    to_display = to_date.strftime('%d/%m/%Y') if to_date else 'Invalid'
                    row_errors.append(f"From Date ({from_display}) cannot be after To Date ({to_display})")
                
                # Check for duplicate errors for this row
                row_duplicate_errors = [err for err in duplicate_errors if f"Row {row_num}:" in err]
                row_errors.extend([err.split(": ", 1)[1] for err in row_duplicate_errors])
            else:
                # If there are base authorization errors, still need to parse dates for processing logic
                from_date, _ = validate_date(row[column_mapping['from_date']], "From Date")
                to_date, _ = validate_date(row[column_mapping['to_date']], "To Date")
            
            # Continue with processing if no errors
            
            # If this row has validation errors, skip processing but record the errors
            if row_errors:
                validation_errors.append(f"Row {row_num}: " + "; ".join(row_errors))
                results.append({
                    "row_number": row_num,
                    "iga_code": iga_code or "invalid",
                    "original_iga": raw_iga_code,
                    "employee_name": "",
                    "leave_period": "",
                    "duration": 0,
                    "result": f"validation_error: " + "; ".join(row_errors)
                })
                error_count += 1
                continue
            
            # Process valid row (IGA existence already validated above)
            # Get name from Excel if available
            excel_name = ""
            if 'name' in column_mapping:
                name_value = row[column_mapping['name']]
                if not pd.isna(name_value) and str(name_value).strip().lower() != 'nan':
                    excel_name = str(name_value).strip()
                
            duration_days = (to_date - from_date).days + 1
            
            # Fetch crew information (we know IGA exists from validation above)
            employee_name = excel_name
            base = ""
            
            try:
                crew_info = await fetch_crew_info(client=client, iga=iga_code)
                if crew_info and isinstance(crew_info, dict) and 'data' in crew_info:
                    # Use Excel name if provided, otherwise use API name
                    if employee_name or not employee_name or employee_name.strip()=='' :
                        employee_name = crew_info['data'].get("name") or crew_info['data'].get("employee_name") or ""
                    base = crew_info['data'].get("base") or ""
            except Exception as crew_error:
                # This shouldn't happen since we validated IGA existence above, but handle gracefully
                logger.warning(f"Error fetching crew info for validated IGA {iga_code}: {crew_error}")
                if not employee_name:
                    employee_name = f"Validated ({iga_code})"
                
            record = {
                "iga_code": iga_code,
                "employee_name": employee_name,
                "base": base,
                "start_date": from_date.strftime('%Y-%m-%d'),
                "end_date": to_date.strftime('%Y-%m-%d'),
                "duration_days": duration_days,
                "comment": None,
                "status": "pending",
                "created_by": {"name": token_user_name or "Unknown", "iga_code": token_iga_code or "ADMIN"},

                "approved_by": None
            }


            try:
                leave_id = upsert_leave_record(record)
                results.append({
                    "row_number": row_num,
                    "iga_code": iga_code,
                    "original_iga": raw_iga_code,
                    "employee_name": employee_name,
                    "leave_period": f"{from_date.strftime('%d/%m/%Y')} to {to_date.strftime('%d/%m/%Y')}",
                    "duration": duration_days,
                    "result": "success",
                    "id": leave_id
                })
                success_count += 1
            except Exception as e:
                results.append({
                    "row_number": row_num,
                    "iga_code": iga_code,
                    "original_iga": raw_iga_code,
                    "employee_name": employee_name,
                    "leave_period": f"{from_date.strftime('%d/%m/%Y')} to {to_date.strftime('%d/%m/%Y')}",
                    "duration": duration_days,
                    "result": f"database_error: {str(e)}"
                })
                error_count += 1
                
        except Exception as row_error:
            results.append({
                "row_number": row_num,
                "iga_code": "unknown",
                "original_iga": raw_iga_code if 'raw_iga_code' in locals() else "unknown",
                "employee_name": "",
                "leave_period": "",
                "duration": 0,
                "result": f"processing_error: {str(row_error)}"
            })
            error_count += 1
    
    # Determine response status based on results
    if error_count == 0 and success_count > 0:
        message = f" Upload successful: All {success_count} records for {user_base} base were uploaded successfully"
        status = "success"
    elif success_count > 0 and error_count > 0:
        message = f" Partial success: {success_count} records for {user_base} base uploaded successfully, {error_count} records failed"
        status = "partial_success"
    else:
        message = f" Upload failed: {error_count} errors found, no records were uploaded for {user_base} base"
        status = "error"
    
    # Prepare detailed error information
    error_details = []
    if validation_errors:
        error_details.extend(validation_errors)
    
    return {
        "status": status,
        "message": message,
        "user_base": user_base,
        "processed": results,
        "summary": {
            "total_rows": len(results),
            "successful": success_count,
            "failed": error_count,
            "authorized_base": user_base
        },
        "error_details": error_details if error_details else None,
        "success_details": [r for r in results if r["result"] == "success"] if success_count > 0 else None,
        "failed_details": [r for r in results if "error" in r["result"]] if error_count > 0 else None
    }

    
@app.get("/genai")
async def index():
    # Generate content
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content("Write a short haiku about Google Cloud Shell using OIDC.")

    return JSONResponse(content={
                "generated_text": response.text
                        })


class QueryRequest(BaseModel):
    """Request model for generic AI search"""

    query: str = Field(..., description="User query string", min_length=1)
    # RENAMED: session_uuid -> chat_thread_id
    chat_thread_id: Optional[str] = Field(
        None, description="Unique ID for conversation thread"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "query": "What is the schedule for IGA 23330?",
                "chat_thread_id": "1234567890",
            }
        }


class SaveChatRequest(BaseModel):
    """Request model to explicitly save chat history (if needed)"""

    chat_thread_id: str = Field(..., description="Thread ID")
    conversation_data: Dict[str, Any] = Field(
        ..., description="Full conversation object"
    )
# ============================================================================
# HELPER: USER ID FROM JWT
# ============================================================================

JWT_SECRET=os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

def get_user_id_from_request(request: Request) -> str:
    """
    Extract user_id (iga_code) from JWT Authorization header.
    Verifies signature using JWT_SECRET and handles 'Bearer Bearer' duplication.
    """
    headers = request.headers
    authorization = headers.get("authorization")
    decoded_token = None
    
    # Default to test_user if extraction fails
    user_id = "test_user"

    if authorization:
        try:
            # FIX: Handle "Bearer Bearer <token>" by taking the last split element
            token = authorization.strip().split()[-1]
            
            # FIX: Pass secret and algorithm to verify the signature
            decoded_token = jwt.decode(
                token, 
                JWT_SECRET, 
                algorithms=[JWT_ALGORITHM]
            )
            
            # Extract iga_code from nested info object
            token_iga_code = None
            if "info" in decoded_token and isinstance(decoded_token["info"], dict):
                token_iga_code = decoded_token["info"].get("iga_code")
            
            
            # Fallback to top-level iga_code
            if not token_iga_code:
                token_iga_code = decoded_token.get("iga_code")
            
            if token_iga_code:
                user_id = str(token_iga_code).strip()
            else:
                # Fallback: Try to use email or sub if iga_code is missing
                token_email = None
                if "info" in decoded_token and isinstance(decoded_token["info"], dict):
                    token_email = decoded_token["info"].get("email")
                
                if not token_email:
                    token_email = decoded_token.get("sub")
                
                if token_email:
                    user_id = str(token_email).strip()
                    logger.warning(f"iga_code not found, using email as user_id: {user_id}")
                else:
                    logger.warning("JWT decoded but no iga_code or email found; using test_user")

        except jwt.ExpiredSignatureError:
            logger.error("Failed to decode JWT token: Token has expired")
        except jwt.InvalidTokenError as e:
            logger.error(f"Failed to decode JWT token: Invalid token - {e}")
        except Exception as e:
            logger.error(f"Failed to decode JWT token: {e}")
    else:
        logger.warning("Missing Authorization header; defaulting to test_user")

    return user_id

# ============================================================================
# API ENDPOINTS
# ============================================================================


@app.post("/api/generic-ai-search")
async def query(request: Request, body: QueryRequest):
    """
    Streaming AI Search Endpoint.
    Returns a stream of Server-Sent Events (SSE).
    Uses JWT to determine user_id (iga_code).
    """
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    # print(request.headers,"---------------------------------------------------------------------")
    
    user_id = get_user_id_from_request(request)
    logger.info(
        f"============ Received query: {body.query} ================="
    )

    async def event_generator():
        try:
            # Execute workflow stream with user_id and chat_thread_id
            async for chunk in app.state.workflow.execute_stream(
                query=body.query,
                user_id=user_id,
                chat_thread_id=body.chat_thread_id,
            ):
                # 'chunk' is already a JSON string from workflow.py
                yield f"data: {chunk}\n\n"

        except Exception as e:
            logger.error(f"Stream error: {str(e)}", exc_info=True)
            error_data = json.dumps(
                {
                    "type": "error",
                    "message": "Internal server error processing request",
                }
            )
            yield f"data: {error_data}\n\n"

        # End of stream indicator
        yield "event: close\ndata: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )



@app.get("/api/fetch-chat-data/{chat_thread_id}")
async def fetch_chat_data(request: Request, chat_thread_id: str):
    """
    Fetch FILTERED conversation history (UI-specific fields only).
    Returns list of messages with: query, answer, charts, insights, timestamp.
    """
    user_id = get_user_id_from_request(request)

    try:
        full_data = fetch_session(user_id, chat_thread_id)

        if not full_data:
            raise HTTPException(status_code=404, detail="Thread not found")

        conversations: List[Dict[str, Any]] = full_data.get("conversations", [])
        filtered_history: List[Dict[str, Any]] = []

        for item in conversations:
            filtered_item = {
                "chat_thread_id": chat_thread_id,
                "user_query": item.get("User", ""),
                "assistant_answer": item.get("Assistant", ""),
                "chart_type": item.get("chart_type", "no_chart"),
                "chart_response": item.get("chart_response", ""),
                "title": item.get("title", ""),
                "insights": item.get("Assistant", ""),
                "actionable_insights": item.get(
                    "Actionable", "EMPTY_ACTIONABLE_INSIGHTS"
                ),
                "timestamp": item.get("timestamp", ""),
            }
            filtered_history.append(filtered_item)

        return {"chat_thread_id": chat_thread_id, "history": filtered_history}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fetch filtered chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/list-chats")
async def list_user_chats(request: Request):
    """
    Fetch all chat sessions for the authenticated user (via JWT).
    Uses the new Index File for fast listing.
    """
    user_id = get_user_id_from_request(request)

    try:
        # This now returns a list of dicts from the Index File
        sessions = fetch_all_user_sessions(user_id)

        summary_list: List[Dict[str, Any]] = []
        for session in sessions:
            # The index file already contains the summary info
            summary_list.append(
                {
                    "chat_thread_id": session.get("chat_thread_id"),
                    "updated_at": session.get("updated_at"),
                    "title": session.get("title", "New Chat"),
                    "turn_count": session.get("turn_count", 0),
                    "created_at": session.get("created_at")
                }
            )

        return {"user_id": user_id, "sessions": summary_list}
    except Exception as e:
        logger.error(f"List chats error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    

    

BQ_DATASET_ID = "flight_reports"
BQ_TABLE_ID = "extracted_documents"



@app.get("/api/flight-report-dashboard")
async def get_comprehensive_dashboard(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    fleet_type: Optional[str] = Query(None),
    departure: Optional[str] = Query(None),
    arrival: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
):
    data = await compute_dashboard_sql(start_date, end_date, fleet_type, departure, arrival, station, client)
    return {
        "success": True,
        "data": data,
        "message": "Comprehensive dashboard data retrieved successfully"
    }

@app.get("/api/flight-report-day-details")
async def get_day_details(
    date: str = Query(..., description="Date in YYYY-MM-DD format to return details for"),
    fleet_type: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
):
    try:
        payload = await day_details_sql(date, fleet_type,client,station)
        return {"success": True, "data": payload, "message": f"Details for {date} retrieved"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@app.get("/api/flight-report-flight-details")
async def get_flight_reg_details(
    flight_reg: str = Query(..., description="Flight registration (Flight Reg) to filter on"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    fleet_type: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
):
    try:
        if not flight_reg:
            return JSONResponse(status_code=400, content={"success": False, "message": "flight_reg is required"})
        payload = await flight_details_sql(flight_reg, start_date, end_date, fleet_type,client,station)
        return {"success": True, "data": payload, "message": f"Details for flight reg {flight_reg} retrieved"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@app.get("/api/flight-report-route-details")
async def get_route_details(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    fleet_type: Optional[str] = Query(None),
    departure: Optional[str] = Query(None),
    arrival: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
):
    try:
        data = await route_details_sql(start_date, end_date, fleet_type, departure, arrival,client,station)
        return {"success": True, "data": data, "message": "Flight route details retrieved successfully"}
    except Exception as e:
        return {"success": False, "message": f"Error retrieving route details: {str(e)}", "data": {}}

@app.get("/api/flight-report-category-details")
async def get_category_details(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    fleet_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
):
    try:
        if not category:
            return {"success": False, "message": "Category parameter is required", "data": {}}
        data = await category_details_sql(start_date, end_date, fleet_type, category,client,station)
        return {"success": True, "data": data, "message": "Category details retrieved successfully"}
    except Exception as e:
        return {"success": False, "message": f"Error retrieving category details: {str(e)}", "data": {}}
    

@app.get("/api/flight-report-ai-analytics")
async def trends() :
    return await compute_combined_summary(client)
       
@app.post("/api/createLeave")
def create_leave(request: LeaveRequest):
    start_date_obj = datetime.strptime(request.start_date, "%Y-%m-%d")
    end_date_obj = datetime.strptime(request.end_date, "%Y-%m-%d")
    duration_days = (end_date_obj - start_date_obj).days + 1

    record = {
        "iga_code": request.iga_code,
        "employee_name": request.employee_name,
        "base": request.base,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "duration_days": duration_days,
        "comment": request.comment,
        "status": "pending",
        "created_by": {"name": request.applied_by_name or "Unknown", "iga_code": request.applied_by or "ADMIN"},
        "status_updated_by": None
    }

    logger.info(f"Creating leave request for {request.iga_code} from {request.start_date} to {request.end_date}")
    try:
        leave_id = upsert_leave_record(record)
        logger.info(f"Leave request created successfully with ID {leave_id}")
        return {
            "message": "Leave request created successfully",
            "id": leave_id,
            "status": "pending",
            "created_by": record["created_by"],
            "status_updated_by": None
        }
    except ValueError as e:
        logger.error(f"Validation error creating leave request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating leave request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/listLeaves")
def list_leaves(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    base: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    iga_code: Optional[str] = Query(None)
):
    filters = {k: v for k, v in {"base": base, "status": status, "iga_code": iga_code}.items() if v}
    logger.info(f"Listing leaves with filters: {filters}, page: {page}, page_size: {page_size}")
    try:
        result = query_paginated_leaves(page, page_size, filters)
        logger.info(f"Found {result['pagination']['total_records']} total leave records")
        return result
    except Exception as e:
        logger.error(f"Error listing leaves: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/list_leaves_analytics")
def list_leaves_analytics(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    base: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    iga_code: Optional[str] = Query(None),
    employee_name: Optional[str] = Query(None)
):
    filters = {k: v for k, v in {"base": base, "status": status, "iga_code": iga_code}.items() if v}
    logger.info(f"Listing leaves analytics with filters: {filters}, page: {page}, page_size: {page_size}")
    try:
        result = query_paginated_leaves(page, page_size, filters)
        logger.info(f"Found {result['pagination']['total_records']} total leave records (analytics)")
        return result
    except Exception as e:
        logger.error(f"Error listing leaves analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/ApproveRejectLeave/{leave_id}")
def approve_or_reject_leave(leave_id: str, action: AdminAction):
    try:
        status = {1: "approved", 0: "rejected"}.get(action.status)
        if status is None:
            logger.error(f"Error updating leave {leave_id}: invalid status value")
            raise HTTPException(status_code=400, detail="invalid status value")

        logger.info(f"Processing leave {leave_id} with action {status}")
        sql = f"SELECT * FROM `{table_ref()}` WHERE id = @id"
        existing_records = query_rows(sql, {"id": leave_id})
        if not existing_records:
            logger.error(f"Leave record {leave_id} not found")
            raise HTTPException(status_code=404, detail="Leave record not found")
        existing_record = existing_records[0]

        updated_record = {
            "id": leave_id,
            "iga_code": existing_record["iga_code"],
            "employee_name": existing_record["employee_name"],
            "base": existing_record["base"],
            "start_date": existing_record["start_date"],
            "end_date": existing_record["end_date"],
            "duration_days": existing_record["duration_days"],
            "comment": action.comment or existing_record.get("comment"),
            "status": status,
            "created_by": existing_record.get("created_by"),
            # "status_updated_by": action.status_updated_by,
            "status_updated_by": action.status_updated_by.model_dump() if action.status_updated_by else None,  # ✅ NEW - Convert Pydantic to dict

            "created_at": existing_record["created_at"]
        }

        upsert_leave_record(updated_record)
        logger.info(f"Leave {leave_id} updated successfully to {status}")
        return {
            "message": f"Leave {status} successfully",
            "id": leave_id,
            "status": status,
            "status_updated_by": updated_record.get("status_updated_by")
        }
    except Exception as e:
        logger.error(f"Error updating leave {leave_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/getEmployeeLeaves/{iga_code}")
def get_employee_leaves(
    iga_code: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    logger.info(f"Fetching leaves for employee {iga_code}, page: {page}, page_size: {page_size}")
    try:
        result = query_paginated_leaves(page, page_size, {"iga_code": iga_code})
        logger.info(f"Found {result['pagination']['total_records']} total leaves for employee {iga_code}")
        return result
    except Exception as e:
        logger.error(f"Error fetching leaves for employee {iga_code}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leaveSummary")
def leave_summary():
    sql = f"""
    SELECT
        COUNT(*) AS total_leaves,
        SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_leaves,
        SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved_leaves,
        SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected_leaves
    FROM `{table_ref()}`
    """
    logger.info("Fetching leave summary")
    try:
        result = query_rows(sql)
        summary = result[0] if result else {
            "total_leaves": 0,
            "pending_leaves": 0,
            "approved_leaves": 0,
            "rejected_leaves": 0
        }
        logger.info(f"Leave summary: {summary}")
        return summary
    except Exception as e:
        logger.error(f"Error fetching leave summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/extract")
async def extract_document(doc_type: str = Form(...), file: UploadFile = File(...)):
    start_time = time.time()
    doc_type_lower = doc_type
    logger.info(f"[{doc_type_lower}] Request received: {file.filename}")

    if not file.filename.lower().endswith(".pdf"):
        logger.warning(f"[{doc_type_lower}] Invalid file type: {file.filename}")
        return JSONResponse({"error": "Only PDF files are supported"}, status_code=400)

    try:
        pdf_bytes = await file.read()
        logger.info(f"[{doc_type_lower}] PDF read successfully: {len(pdf_bytes)} bytes")

        result = await doc_extractor(pdf_bytes, doc_type_lower)
        process_time = time.time() - start_time
        logger.info(f"[{doc_type_lower}] Extraction completed in {process_time:.2f} seconds")

        return result

    except Exception as e:
        logger.exception(f"[{doc_type_lower}] Extraction failed")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    
# @app.post("/api/upload")
# async def upload_document(
#     doc_type: str = Form(...),
#     igacode: str = Form(...),
#     name: str = Form(...),
#     base: str = Form(...),
#     extracted_data: str = Form(...),
#     file: UploadFile = File(...)
# ):
#     try:
#         # Initialize clients
#         db = get_firestore_client(database=FIRESTORE_DATABASE_ID)
#         collection_ref = db.collection(FIRESTORE_COLLECTION_ID)
        
#         # Normalize inputs
#         doc_type_lower = doc_type.strip().lower()
#         timestamp_str = datetime.now().strftime("%d-%m-%Y_%H%M%S")
#         safe_name = name.strip().replace(" ", "_").lower()
#         safe_doc_type = doc_type_lower.replace(" ", "_")
#         safe_igacode = igacode.strip().replace(" ", "_").upper()
 
#         # Final filename
#         final_filename = f"{safe_igacode}-{safe_name}-{safe_doc_type}-{timestamp_str}.pdf"
#         logger.info(f"[{safe_doc_type}] Upload request received: {final_filename}")

#         # Validate file type
#         if not file.filename.lower().endswith(".pdf"):
#             return JSONResponse({"error": "Only PDF files are supported"}, status_code=400)

#         # Read and Process file
#         pdf_bytes = await file.read()
#         try:
#             corrected_pdf_bytes, detected_angles = detect_rotation_template_matching(pdf_bytes)
#         except Exception as e:
#             logger.warning(f"[{safe_doc_type}] Orientation fix failed: {e}")
#             corrected_pdf_bytes = pdf_bytes 

#         # Upload to GCS
#         bucket = get_bucket(GCS_BUCKET_NAME)
#         blob = bucket.blob(final_filename)
#         blob.upload_from_string(corrected_pdf_bytes, content_type="application/pdf")
#         gcs_url = f"gs://{GCS_BUCKET_NAME}/{final_filename}"

#         # Parse extracted_data
#         try:
#             parsed_data = json.loads(extracted_data)
#         except json.JSONDecodeError:
#             parsed_data = extracted_data

#         # Firestore document ID
#         doc_id = f"{safe_igacode}_{safe_name}"
#         doc_ref = collection_ref.document(doc_id)
#         existing_doc = doc_ref.get()

#         # Prepare payload for this new upload
#         new_payload = {
#             "id": str(uuid4()),
#             "file_name": final_filename,
#             "gcs_url": gcs_url,
#             "extracted_data": parsed_data,
#             "timestamp": datetime.now(timezone.utc),
#             "status": "pending"
#         }

#         if existing_doc.exists:
#             logger.info(f"[{safe_doc_type}] Updating existing Firestore doc: {doc_id}")
#             existing_data = existing_doc.to_dict()
            
#             # Get the current list for this doc_type, or empty list if new type
#             current_list = existing_data.get(safe_doc_type, [])
            
#             # --- LOGIC CHANGE START ---
#             updated_list = []
            
#             # Loop through existing items to handle previous "pending" ones
#             for item in current_list:
#                 if item.get("status") == "pending":
#                     # Mark the old pending item as superseded (or cancelled)
#                     # This ensures only the NEWEST one remains 'pending'
#                     item["status"] = "superseded" 
#                     item["superseded_at"] = datetime.now(timezone.utc)
#                 updated_list.append(item)
            
#             # Append the new payload
#             updated_list.append(new_payload)
            
#             # Update the specific field with the REPLACED list (not ArrayUnion)
#             doc_ref.update({
#                 safe_doc_type: updated_list
#             })
#             # --- LOGIC CHANGE END ---
            
#         else:
#             logger.info(f"[{safe_doc_type}] Creating new Firestore doc: {doc_id}")
#             # Use specific doc_type as key, containing a list with the payload
#             doc_ref.set({
#                 "igacode": igacode,
#                 "name": name,
#                 "base": base,
#                 safe_doc_type: [new_payload]
#             })

#         return JSONResponse({"status": "success", "message": "Document uploaded successfully"}, status_code=200)
    
#     except Exception as e:
#         logger.exception(f"[{doc_type}] Upload failed")
#         return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    
def rotate_pdf_background(final_filename, pdf_bytes, doc_ref_path, safe_doc_type):
    """
    Rotate PDF, upload to GCS, and update Firestore PDF gcs_url field safely.
    """
    from rotate_pdf import detect_rotation_template_matching
    try:
        corrected_pdf_bytes, _ = detect_rotation_template_matching(pdf_bytes)

        # Upload rotated PDF to GCS
        bucket = get_bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(final_filename)
        blob.upload_from_string(corrected_pdf_bytes, content_type="application/pdf")
        rotated_url = f"gs://{GCS_BUCKET_NAME}/{final_filename}"

        # Update only the gcs_url in Firestore
        db = get_firestore_client(database=FIRESTORE_DATABASE_ID)
        doc_ref = db.document(doc_ref_path)
        doc_snapshot = doc_ref.get()
        if doc_snapshot.exists:
            data = doc_snapshot.to_dict()
            # Find the pending upload in the list and update its gcs_url
            updated_list = []
            for item in data.get(safe_doc_type, []):
                if item.get("status") == "pending" and item.get("file_name") == final_filename:
                    item["gcs_url"] = rotated_url
                updated_list.append(item)
            doc_ref.update({safe_doc_type: updated_list})

        print(f"[{safe_doc_type}] Rotated PDF uploaded and Firestore updated: {final_filename}")

    except Exception as e:
        print(f"[{safe_doc_type}] Background rotation failed: {e}")


@app.post("/api/upload")
async def upload_document(
    doc_type: str = Form(...),
    igacode: str = Form(...),
    name: str = Form(...),
    base: str = Form(...),
    extracted_data: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        db = get_firestore_client(database=FIRESTORE_DATABASE_ID)
        collection_ref = db.collection(FIRESTORE_COLLECTION_ID)
        bucket = get_bucket(GCS_BUCKET_NAME)

        # Normalize inputs
        safe_name = name.strip().replace(" ", "_").lower()
        safe_doc_type = doc_type.strip().lower().replace(" ", "_")
        safe_igacode = igacode.strip().replace(" ", "_").upper()
        timestamp_str = datetime.now().strftime("%d-%m-%Y_%H%M%S")
        final_filename = f"{safe_igacode}-{safe_name}-{safe_doc_type}-{timestamp_str}.pdf"

        if not file.filename.lower().endswith(".pdf"):
            return JSONResponse({"error": "Only PDF files are supported"}, status_code=400)

        pdf_bytes = await file.read()

        # Parse extracted data
        try:
            parsed_data = extracted_data
        except Exception:
            parsed_data = extracted_data

        # Firestore doc ID
        doc_id = f"{safe_igacode}_{safe_name}"
        doc_ref = collection_ref.document(doc_id)
        existing_doc = doc_ref.get()

        # Prepare new payload
        new_payload = {
            "id": str(uuid4()),
            "file_name": final_filename,
            "gcs_url": f"gs://{GCS_BUCKET_NAME}/{final_filename}",  # initial uploaded PDF
            "extracted_data": json.dumps(parsed_data),
            "timestamp": datetime.now(timezone.utc),
            "status": "pending"
        }

        # Update Firestore using old logic (supersede pending, append new)
        if existing_doc.exists:
            existing_data = existing_doc.to_dict()
            current_list = existing_data.get(safe_doc_type, [])

            updated_list = []
            for item in current_list:
                if item.get("status") == "pending":
                    item["status"] = "superseded"
                    item["superseded_at"] = datetime.now(timezone.utc)
                updated_list.append(item)

            updated_list.append(new_payload)
            doc_ref.update({safe_doc_type: updated_list})
        else:
            doc_ref.set({
                "igacode": igacode,
                "name": name,
                "base": base,
                safe_doc_type: [new_payload]
            })

        # Upload original PDF immediately
        blob = bucket.blob(final_filename)
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")

        # Run PDF rotation asynchronously
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            app.state.executor,
            rotate_pdf_background,
            final_filename,
            pdf_bytes,
            doc_ref.path,
            safe_doc_type
        )

        return JSONResponse({"status": "success", "message": "Document uploaded successfully"}, status_code=200)

    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/search_crew")
async def search_crew(
    iga: str | None = Query(None, description="IGA code, e.g., 23330"),
    base: str | None = Query(None, description="Base, e.g., DEL"),
    position: str | None = Query(None, description="Position, e.g., CA/CP/CC"),
):
    try:
        data = await fetch_crew_info(client=client,iga=iga)
        
        return {"results": data}
    except ValueError as ve:
        logger.error(f"Validation error in search_crew: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception(f"Unexpected error in search_crew: {e}")
        raise HTTPException(status_code=500, detail="Internal server error occurred while searching crew.")
    
@app.post("/api/documentationAgent")
async def documentation_agent(
    query: str = Form(None),
    file: UploadFile = File(None)
):
    """
    Documentation agent for visa document checking.
    
    Usage:
    - Query: Visa type to apply for (e.g., "Turkey Corendon Wetlease Visa")
    - Excel File: Contains IGACode column with employee IDs who need this visa
    
    The system will check for each employee what documents they have vs what's required for the visa type.
    """
    try:
        # Both query and file are required
        if not query:
            raise HTTPException(status_code=400, detail="Query (visa type) is required.")
        
        if not file:
            raise HTTPException(status_code=400, detail="Excel file with IGACodes is required.")
        
        # Validate file type
        if not file.filename.endswith((".xlsx", ".xls")):
            raise HTTPException(status_code=400, detail="Only Excel files (.xlsx, .xls) are supported.")
        
        # Read file content
        contents = await file.read()
        logger.info(f"Processing visa documentation request:")
        logger.info(f"  Visa type: {query}")
        logger.info(f"  Excel file: {file.filename}")
        
        # Use the unified function with both query and excel_file_content
        data = await documentationAgent(query, excel_file_content=contents)
        
        return {"results": data, "type": "bulk_processing"}
            
    except ValueError as ve:
        logger.error(f"Error in documentation_agent: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception(f"Unexpected error in documentation_agent: {e}")
        raise HTTPException(status_code=500, detail="Internal server error occurred while processing documentation agent.")

@app.get("/api/documents/{igacode}")
async def list_documents_by_iga(igacode: str):
    """
    List all documents for a specific IGA code (supports new list-based schema with fallback).
    """
    try:
        logger.info(f"Fetching documents for IGA code: {igacode}")
        db=get_firestore_client(database=FIRESTORE_DATABASE_ID)

        # db = firestore.Client(
        #     project=GCP_PROJECT_ID,
        #     database=FIRESTORE_DATABASE_ID
        # )

        collection_ref = db.collection(FIRESTORE_COLLECTION_ID)
        docs = collection_ref.where("igacode", "==", igacode).stream()

        documents = []

        for doc in docs:
            doc_data = doc.to_dict()
            doc_id = doc.id

            igacode_val = doc_data.get("igacode")
            name_val = doc_data.get("name")

            for key, value in doc_data.items():

                # Skip meta fields
                if key in ["igacode", "name"]:
                    continue

                # ---------------------------------------------------------
                # CASE 1 — NEW SCHEMA (value is a list of document objects)
                # ---------------------------------------------------------
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "file_name" in item:

                            file_name = item["file_name"]

                            documents.append({
                                "firestore_doc_id": doc_id,
                                "igacode": igacode_val,
                                "name": name_val,
                                "doc_type": key,
                                "file_name": file_name,
                                "id": item.get("id"),
                                "timestamp": item.get("timestamp"),
                                "extracted_data":json.loads(item.get("extracted_data")) if isinstance(item.get("extracted_data"), str) else item.get("extracted_data"),
                                "preview_url": f"/api/documents/preview/{file_name}",
                                "download_url": f"/api/documents/download/{file_name}",
                                "status": item.get("status", "pending"),
                                "approved_by": item.get("approved_by", ""),
                                "rejected_by": item.get("rejected_by", ""),
                                "comment": item.get("comment", "")
                            })

                # ---------------------------------------------------------
                # CASE 2 — OLD SCHEMA (single dict with file_name)
                # ---------------------------------------------------------
                elif isinstance(value, dict) and "file_name" in value:

                    file_name = value["file_name"]

                    documents.append({
                        "firestore_doc_id": doc_id,
                        "igacode": igacode_val,
                        "name": name_val,
                        "doc_type": key,
                        "file_name": file_name,
                        "id": value.get("id"),
                        "timestamp": value.get("timestamp"),
                        "extracted_data":json.loads(value.get("extracted_data")) if isinstance(value.get("extracted_data"), str) else value.get("extracted_data"),
                        "preview_url": f"/api/documents/preview/{file_name}",
                        "download_url": f"/api/documents/download/{file_name}",
                        "status": value.get("status", "pending"),
                        "approved_by": value.get("approved_by", ""),
                        "rejected_by": value.get("rejected_by", ""),
                        "comment": value.get("comment", "")
                    })

        return {
            "igacode": igacode,
            "documents": documents,
            "total_documents": len(documents),
            "status": "success"
        }

    except Exception as e:
        logger.exception(f"Error fetching documents for IGA code {igacode}: {e}")
        raise HTTPException(500, f"Failed to fetch documents: {str(e)}")


# @app.get("/api/ListAlldocuments")
# async def list_all_documents(base: str | None = Query(None)):
#     """
#     List ALL documents in the Firestore collection.
#     Supports list-of-documents for each doc_type.
#     """
#     try:
#         logger.info("Fetching ALL documents")
        
#         # db = firestore.Client(
#         #     project=GCP_PROJECT_ID, 
#         #     database=FIRESTORE_DATABASE_ID
#         # )
#         db=get_firestore_client(database=FIRESTORE_DATABASE_ID)
        
#         collection_ref = db.collection(FIRESTORE_COLLECTION_ID)
#         if base:
#             collection_ref = collection_ref.where("base", "==", base)
#         docs = collection_ref.stream()

#         documents = []

#         for doc in docs:
#             doc_data = doc.to_dict()
#             doc_id = doc.id

#             igacode = doc_data.get("igacode")
#             name = doc_data.get("name")

#             # Loop through all fields of this Firestore document
#             for key, value in doc_data.items():

#                 # Skip metadata fields
#                 if key in ["igacode", "name"]:
#                     continue

#                 # Case 1: Field is a list of uploaded files
#                 if isinstance(value, list):
#                     for item in value:
#                         if isinstance(item, dict) and "file_name" in item:
                            
#                             file_name = item["file_name"]

#                             preview_url = f"/api/documents/preview/{file_name}"
#                             download_url = f"/api/documents/download/{file_name}"
#                             #print(item,"-----------------------------------------------------------")
#                             documents.append({
#                                 "firestore_doc_id": doc_id,
#                                 "igacode": igacode,
#                                 "name": name,
#                                 "doc_type": key,
#                                 "file_name": file_name,
#                                 "id":item.get("id"),
#                                 "timestamp": item.get("timestamp"),
#                                 "extracted_data": item.get("extracted_data"),
#                                 "preview_url": preview_url,
#                                 "download_url": download_url,
#                                 "status": item.get("status", "pending"),
#                                 "approved_by": item.get("approved_by", ""),
#                                 "rejected_by": item.get("rejected_by", ""),
#                                 "comment": item.get("comment", "")
#                             })

#                 # Case 2: (fallback) Single object (old data format)
#                 elif isinstance(value, dict) and "file_name" in value:

#                     file_name = value["file_name"]

#                     preview_url = f"/api/documents/preview/{file_name}"
#                     download_url = f"/api/documents/download/{file_name}"

#                     documents.append({
#                         "firestore_doc_id": doc_id,
#                         "igacode": igacode,
#                         "name": name,
#                         "doc_type": key,
#                         "file_name": file_name,
#                         "timestamp": value.get("timestamp"),
#                          "id":value.get("id"),
#                         "extracted_data": value.get("extracted_data"),
#                         "preview_url": preview_url,
#                         "download_url": download_url,
#                         "status": value.get("status", "pending"),
#                         "approved_by": value.get("approved_by", ""),
#                         "rejected_by": value.get("rejected_by", ""),
#                         "comment": value.get("comment", "")
#                     })

#         return {
#             "documents": documents,
#             "total_documents": len(documents),
#             "status": "success"
#         }

#     except Exception as e:
#         logger.exception(f"Error fetching documents: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ListAlldocuments")
async def list_all_documents(base: str | None = Query(None)):
    """
    List ALL documents in the Firestore collection.
    Filters out 'superseded' documents.
    """
    try:
        logger.info("Fetching ALL documents")
        
        db = get_firestore_client(database=FIRESTORE_DATABASE_ID)
        
        collection_ref = db.collection(FIRESTORE_COLLECTION_ID)
        if base:
            collection_ref = collection_ref.where("base", "==", base)
        docs = collection_ref.stream()

        documents = []

        for doc in docs:
            doc_data = doc.to_dict()
            doc_id = doc.id

            igacode = doc_data.get("igacode")
            name = doc_data.get("name")
            base= doc_data.get("base")

            # Loop through all fields of this Firestore document
            for key, value in doc_data.items():

                # Skip metadata fields
                if key in ["igacode", "name","base"]: # Added 'base' to skip list
                    continue

                # Case 1: Field is a list of uploaded files (New Format)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "file_name" in item:
                            
                            # --- FILTER LOGIC START ---
                            # If status is superseded, skip this item entirely
                            if item.get("status") == "superseded":
                                continue
                            # --- FILTER LOGIC END ---

                            file_name = item["file_name"]
                            preview_url = f"/api/documents/preview/{file_name}"
                            download_url = f"/api/documents/download/{file_name}"

                            documents.append({
                                "firestore_doc_id": doc_id,
                                "igacode": igacode,
                                "base":base,
                                "name": name,
                                "doc_type": key,
                                "file_name": file_name,
                                "id": item.get("id"),
                                "timestamp": item.get("timestamp"),
                                "extracted_data": json.loads(item.get("extracted_data")) if isinstance(item.get("extracted_data"), str) else item.get("extracted_data"),
                                "preview_url": preview_url,
                                "download_url": download_url,
                                "status": item.get("status", "pending"),
                                "approved_by": item.get("approved_by", ""),
                                "rejected_by": item.get("rejected_by", ""),
                                "comment": item.get("comment", "")
                            })

                # Case 2: (fallback) Single object (Old data format)
                elif isinstance(value, dict) and "file_name" in value:
                    
                    # --- FILTER LOGIC START ---
                    if value.get("status") == "superseded":
                        continue
                    # --- FILTER LOGIC END ---

                    file_name = value["file_name"]
                    preview_url = f"/api/documents/preview/{file_name}"
                    download_url = f"/api/documents/download/{file_name}"

                    documents.append({
                        "firestore_doc_id": doc_id,
                        "igacode": igacode,
                        "name": name,
                        "doc_type": key,
                        "file_name": file_name,
                        "timestamp": value.get("timestamp"),
                        "id": value.get("id"),
                        "extracted_data": json.loads(value.get("extracted_data")) if isinstance(value.get("extracted_data"), str) else value.get("extracted_data"),
                        "preview_url": preview_url,
                        "download_url": download_url,
                        "status": value.get("status", "pending"),
                        "approved_by": value.get("approved_by", ""),
                        "rejected_by": value.get("rejected_by", ""),
                        "comment": value.get("comment", "")
                    })

        return {
            "documents": documents,
            "total_documents": len(documents),
            "status": "success"
        }

    except Exception as e:
        logger.exception(f"Error fetching documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/update_document_status")
async def update_document_status(payload: StatusUpdateRequest):
    try:
        # db = firestore.Client(project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE_ID)
        db=get_firestore_client(database=FIRESTORE_DATABASE_ID)
        
        doc_ref = db.collection(FIRESTORE_COLLECTION_ID).document(payload.firestore_doc_id)

        doc_snapshot = doc_ref.get()

        if not doc_snapshot.exists:
            raise HTTPException(404, "Document ID not found in Firestore")

        data = doc_snapshot.to_dict()
        doc_type = payload.doc_type

        if doc_type not in data:
            raise HTTPException(404, f"Document type '{doc_type}' not found")

        # Determine status value
        if payload.action == 1:
            status_value = "approved"
        elif payload.action == 0:
            status_value = "rejected"
        else:
            raise HTTPException(400, "Invalid action value; must be 1 or 0")

        # Get doc_type list
        items = data.get(doc_type)

        # ---------- FALLBACK MECHANISM ----------
        if items is None:
            items = []

        elif isinstance(items, dict):
            # Convert single object → list
            items = [items]

        elif not isinstance(items, list):
            # Convert any other non-list into list
            items = [items]

        # Always fix Firestore structure (self-healing)
        doc_ref.update({doc_type: items})
        # ----------------------------------------

        updated = False

        # Update only using the unique ID
        for item in items:
            if item.get("id") == payload.id:
                item["status"] = status_value
                # Validate comment, if invalid → store empty string
                item["comment"] = validate_comment(payload.comment)

                item["approved_by"] = payload.approved_by if status_value == "approved" else ""
                item["rejected_by"] = payload.rejected_by if status_value == "rejected" else ""
                updated = True
                break

        if not updated:
            raise HTTPException(404, "Document version with given ID not found")

        # Write back final updated list
        doc_ref.update({doc_type: items})

        return {
            "status": "success",
            "message": f"Document ID {payload.id} marked as {status_value}",
            "updated_status": status_value
        }

    except Exception as e:
        raise HTTPException(500, detail=str(e))



@app.get("/api/documents/preview/{file_name}")
async def preview_pdf(file_name: str):
    """Preview the PDF in browser (inline display)."""
    storage_client = storage.Client(project=GCP_PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(file_name)
    pdf_bytes = blob.download_as_bytes()

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={file_name}"}
    )


@app.get("/api/documents/download/{file_name}")
async def download_pdf(file_name: str):
    """Force PDF download."""
    storage_client = storage.Client(project=GCP_PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(file_name)
    pdf_bytes = blob.download_as_bytes()

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={file_name}"}
    )

@app.get("/api/crew_profile")
async def crew_profile(  background_tasks: BackgroundTasks,iga_code: str | None = Query(None, description="IGA code, e.g., 23330"),
    base: str | None = Query(None, description="Base, e.g., DEL")):
    """Fast API — returns merged data immediately; triggers insights in background."""
    try:
        if base is None:
            base=" "
        merged = await fetch_and_merge_crew_data(base,iga_code)
        csv_data = await load_filtered_iga_data("CrewProfile/ifs_data.csv", iga_code)

        # Kick off insights computation in background
        background_tasks.add_task(generate_and_store_insights, merged, csv_data, iga_code)

        # Return base data immediately
        return {"iga_code": iga_code, "csv_records": csv_data, **merged, "status": "processing_insights"}

    except Exception as e:
        logger.error(f"Profile generation failed: {e}")
        return {"error": str(e)}


@app.get("/api/crew_insights/{iga_code}")
async def crew_insights(iga_code: str):
    insights = insights_cache.get(iga_code)
    if not insights:
        return {"status": "processing"}
    return {"status": "ready", "insights": insights}

# @app.get("/{full_path:path}")
# async def spa_routes(full_path: str):
#     # non-API routes
#     if full_path.startswith("api"):
#         return {"detail": "Not Found"}

#     index_path = os.path.join("dist", "index.html")
#     return FileResponse(index_path)


if __name__ == "__main__":
    import uvicorn
    logger.info("[STARTUP] Starting FastAPI backend with integrated scheduler...")
    logger.info("[SCHEDULER] Scheduled Jobs:")
    logger.info("   - Mail Payload API Update (All Documents): Daily at 6:00 AM")
    logger.info("   - Mail Payload Evening API Update: Daily at 6:00 PM")
    logger.info("[SCHEDULER] Documents processed: AEP, PVC, CMED, PASSPORT, WMED")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
