#Testing for FastMCP Host
import os
import time
import json
import logging
import base64
import httpx
import asyncio
import requests
import pyodbc
from typing import Dict, Any
from datetime import datetime, timedelta,date
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from google.cloud import bigquery
from dotenv import load_dotenv
load_dotenv()

# ---------------- Environment (Navitaire) ----------------
NAVITAIRE_BASE_URL = os.getenv("NAVITAIRE_BASE_URL")
NAVITAIRE_USERNAME = os.getenv("NAVITAIRE_USERNAME")
NAVITAIRE_ALT_ID = os.getenv("NAVITAIRE_ALT_ID")
NAVITAIRE_PASSWORD = os.getenv("NAVITAIRE_PASSWORD")
NAVITAIRE_DOMAIN = os.getenv("NAVITAIRE_DOMAIN")
NAVITAIRE_CHANNEL_TYPE = os.getenv("NAVITAIRE_CHANNEL_TYPE")
NAVITAIRE_LOCATION_CODE = os.getenv("NAVITAIRE_LOCATION_CODE")
NAVITAIRE_ORG_CODE = os.getenv("NAVITAIRE_ORG_CODE")
NAVITAIRE_LOGIN_ROLE = os.getenv("NAVITAIRE_LOGIN_ROLE")
NAVITAIRE_APPLICATION_NAME = os.getenv("NAVITAIRE_APPLICATION_NAME")
NAVITAIRE_MANIFEST_LEGKEY_ENDPOINT = os.getenv("NAVITAIRE_MANIFEST_LEGKEY_ENDPOINT")
NAVITAIRE_GET_MANIFEST_ENDPOINT = os.getenv("NAVITAIRE_GET_MANIFEST_ENDPOINT")
NAVITAIRE_BOOKING_BY_RL_ENDPOINT = os.getenv("NAVITAIRE_BOOKING_BY_RL_ENDPOINT")
NAV_USER_KEY = os.getenv("NAV_USER_KEY")
NAVITAIRE_AUTH_SCHEME = os.getenv("NAVITAIRE_AUTH_SCHEME", "Bearer")
NAVITAIRE_EXTRA_HEADERS_RAW = os.getenv("NAVITAIRE_EXTRA_HEADERS")

#-----------------Salesforce Environment-----------------
SF_TOKEN_URL = os.getenv("SF_TOKEN_URL")
SF_CLIENT_ID = os.getenv("SF_CLIENT_ID")
SF_CLIENT_SECRET = os.getenv("SF_CLIENT_SECRET")
SF_GRANT_TYPE = os.getenv("SF_GRANT_TYPE")
SF_JOB_QUERY_URL = os.getenv("SF_JOB_QUERY_URL")

#-----------------JOC Environment-----------------
JOC_TOKEN_ENDPOINT = os.getenv("JOC_TOKEN_ENDPOINT")
JOC_FLIGHT_TIME_ENDPOINT = os.getenv("JOC_FLIGHT_TIME_ENDPOINT")
JOC_CLIENT_ID = os.getenv("JOC_CLIENT_ID")
JOC_CLIENT_SECRET = os.getenv("JOC_CLIENT_SECRET")
JOC_SCOPE = os.getenv("JOC_SCOPE")
JOC_USER_KEY = os.getenv("JOC_USER_KEY")

# ---------------- Environment (SQL Server) ----------------
DB_SERVER = os.getenv("DB_SERVER", "")
DB_DATABASE = os.getenv("DB_DATABASE", "")
DB_USERNAME = os.getenv("CREWPORTAL_DB_USERNAME", "")
DB_PASSWORD = os.getenv("CREWPORTAL_DB_PASSWORD", "")
USE_SERVICE_ACCOUNT = os.getenv("USE_SERVICE_ACCOUNT", "False").lower() == "true"
# ---------------- Environment (AIMS) ----------------
AIMS_CLIENT_ID = os.getenv("AIMS_CLIENT_ID")
AIMS_BASE = os.getenv("AIMS_BASE_URL")
AIMS_AUTH = os.getenv("AIMS_AUTH_URL")
USER_KEY = os.getenv("AIMS_USER_KEY")
CREW_DECRYPTION_KEY = os.getenv("CREW_DECRYPTION_KEY")

# ---------------- Logging ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_level = getattr(logging, LOG_LEVEL, logging.INFO)
logging.basicConfig(
    level=_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("6e-mcp-server-core")

mcp = FastMCP(name="6e-mcp-server-core")

# Log registered routes for debugging
log.info("FastMCP instance created")

#----------------big query-------------------
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Simple health check endpoint"""
    log.info("Health check endpoint called")
    health_status = {
        "status": "OK",
        "timestamp": datetime.now().isoformat()
    }
    return PlainTextResponse(
        json.dumps(health_status, indent=2),
        status_code=200,
        headers={"Content-Type": "application/json"}
    )

@mcp.custom_route("/tools", methods=["GET"])
async def tools_info(request: Request) -> PlainTextResponse:
    """Get information about all registered MCP tools"""
    log.info("Tools info endpoint called")
    try:
        tools_info = {
            "status": "OK",
            "timestamp": datetime.now().isoformat(),
            "tools": []
        }
        
        # Get tools using FastMCP's list_tools method
        try:
            all_tools = await mcp.list_tools()
            for tool in all_tools:
                tools_info["tools"].append({
                    "name": tool.name,
                    "description": (tool.description or "No description available")
                })
        except Exception as e:
            log.debug(f"Could not fetch tools via list_tools: {e}")
            # Fallback to hardcoded list
            known_tools = [
                ("bigquery_flight_report", "Execute SQL queries against BigQuery flight report datasets"),
                ("bigquery_nps", "Execute SQL queries against BigQuery NPS datasets for customer satisfaction analysis"),
                ("crew_info_by_iga", "Fetch crew information for a given IGA code from AIMS"),
                ("crew_roster_info", "Fetch crew roster data from AIMS for a given date range"),
                ("qualification_info_by_iga", "Fetch crew qualifications data from AIMS"),
                ("flight_data_by_joc", "Fetch flight data from JOC (Jeppsen Operations Control)"),
                ("missing_flight_reports", "Identify flights scheduled in JOC but missing flight reports in BigQuery"),
                ("CDP_data_by_nav", "Fetch manifest leg key, manifest, and booking details from Navitaire"),
                ("navitaire_flight_search", "Search flights in Navitaire system"),
                ("salesforce_query_tool", "Query Salesforce data"),
                ("flight_schedule_data", "Fetch flight schedule data"),
                ("check_breathalyzer", "Check breathalyzer status for crew"),
                ("snowflake_survey_salesforce", "Execute queries against Snowflake survey data"),
                ("crewportal_sqlserver", "Execute SQL queries against SQL Server database"),
                ("missing_flight_report_db", "Get missing flight reports from database")
            ]
            for tool_name, description in known_tools:
                tools_info["tools"].append({
                    "name": tool_name,
                    "description": description
                })
        
        # Sort tools alphabetically
        tools_info["tools"].sort(key=lambda x: x["name"])
        
        return PlainTextResponse(
            json.dumps(tools_info, indent=2),
            status_code=200,
            headers={"Content-Type": "application/json"}
        )
        
    except Exception as e:
        log.error(f"Error in tools_info endpoint: {str(e)}")
        error_response = {
            "status": "ERROR",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }
        return PlainTextResponse(
            json.dumps(error_response, indent=2),
            status_code=500,
            headers={"Content-Type": "application/json"}
        )

@mcp.tool("bigquery_flight_report")
async def bigquery_run_query(query: str):
    """
bigquery_flight_report Tool Description:

Overview:
Executes SQL queries against BigQuery flight report datasets with comprehensive field mapping and predefined category classifications. Provides access to detailed flight reports.

Core Functionality:
- Primary Use: SQL queries against flight reports
- Security: SELECT-only queries
- Categories: Predefined issue classifications with detailed sub-categories

Input/Output Specification:

Input Format:
{"query": "SELECT * FROM table_name WHERE Flight_Date_Time > '2025-01-01'"}

Query Requirements:
- Allowed: Only SELECT statements 
- Syntax: Standard BigQuery SQL 

Output Format:
{
  "result": "[{\"Fleet_Type\":\"320\",\"Flight_Date_Time\":\"2025-01-15T06:30:00\",\"Created\":\"2025-01-15T18:45:00\",\"Flight_Number\":\"6E2386\",\"Sector\":\"DEL-BOM\",\"Category\":\"Catering Issues\",\"Sub_category\":\"Quality of Perishable Item\",\"Description\":\"Passenger complained about stale upma served during breakfast service\",\"Resolution_Short_Description\":\"Catering supplier notified. Alternative meals provided\",\"State\":\"Resolved\",\"Number\":\"INC0012345\"}]"
}

Data Fields:

Complete Table Schema:
The flight reports table contains the following fields:

Flight Operations:
- Fleet_Type (STRING): Aircraft type (320, 321, ATR)
- Flight_Number (STRING): Flight identifier (6E-6740)
- Flight_Reg (STRING): Aircraft registration/tail number (VTIMI)
- Sector (STRING): Flight route (DEP-ARR format) (BOM-CCU)
- Station (STRING): Airport where issue originated (BOM)
- Flight_Date_Time (TIMESTAMP): Scheduled flight date/time(for eg. '2025-11-04 04:35:00 UTC')

Crew Information:
- LEAD_BASE (STRING): Lead cabin crew base (DEL)
- L1, L4, R1, R4, R3 (STRING): Cabin crew positions (IGA code and name)(IGA12345 - John Doe)
- FO (STRING): First Officer details (IGA code and name)  (IGA12345 - John Doe)
- Captain (STRING): Captain details (IGA code and name) (IGA12345 - John Doe)
- Crew_Involved (STRING): Specific crew positions involved in issue (L1 or L1,R1 or NA)

Issue Classification:
- Category (STRING): Primary issue category (Catering Issues, Crew Feedback, etc.)
- Sub_category (STRING): Detailed issue subcategory
- Description (STRING): Comprehensive issue description
- Resolution_Short_Description (STRING): Summary of resolution actions
- State (STRING): Issue status (Assigned, In Progress, Resolved, Canceled, Closed)
- Resolution_code (STRING): Resolution classification code(e.g. 'Resolved', 'Resolved by caller', 'Resolved with review', 'Resolved by Agent', 'Assigned')
- Resolved_by (STRING): Person/team who resolved issue

Cockpit Meal Specific Fields (IMPORTANT for meal-related queries):
- LeadAskedForCockpitMeal (STRING): Whether lead requested cockpit meal (Yes/No)
- LeadAskedForCockpitMealComments (STRING): Comments about cockpit meal request
- CockpitMealOrderGivenOnGround (STRING): Whether meal order was placed on ground (Yes/No)
- CockpitMealOrderMsg (STRING): Message/details about cockpit meal order

Service Quality Assessment:
- Thank_you_Service (STRING): Thank you service completion status
- ThankyouServiceComments (STRING): Comments on thank you service
- SecondroundofFBService (STRING): Second round FB service status
- SecondroundofFBServiceComments (STRING): Comments on second round service
- Cabin_Service (STRING): Overall cabin service rating
- Cabin_Service_Comments (STRING): Cabin service feedback
- ServiceCompleted (STRING): Service completion comments
- ServiceCompletedComments (STRING): Service completion status (Yes/No)
- Deviation (STRING): Any flight deviations : Yes/No or empty
- DeviationComments (STRING): Comments about deviations

Administrative Fields:
- Delay_in_days (STRING): Human-readable delay description- consist of difference between created and Flight_Date_Time column(e.g. '3 Hours 42 Minutes', '1 Day 14 Hours 4 Minutes') (Disclaimer: This is not actual flight delay)
- CDLB_Entry_Made (STRING): Cabin Defect Log Book entry status(Yes/No/Not Applicable)
- Cabin_Type (STRING): Aircraft cabin configuration (Not Applicable, E/Y, Both, IndiGoStretch, EQUIP, O)
- Number (STRING): ServiceNow ticket number (format: INC2636379)
- Has_breached (STRING): SLA breach indicator (FALSE)
- customerExperienceChampion (STRING): Customer experience champion details (L1,R1 or L2,R1, or other combination20or N/A)
- Tag_1, Tag_2 (STRING): Additional categorization tags
- Created (TIMESTAMP): Flight report creation timestamp (2025-11-04 04:35:00 UTC)
- Resolved (TIMESTAMP): Issue resolution timestamp (2025-06-29T01:35:00+05:30 or it can be blank)


CRITICAL: For Cockpit Meal Queries:
When querying for cockpit meal related reports, use these specific columns:
- LeadAskedForCockpitMeal = 'Yes' (for reports where lead requested cockpit meal)
- CockpitMealOrderGivenOnGround = 'Yes' (for reports where meal order was placed)
- LeadAskedForCockpitMealComments IS NOT NULL (for reports with meal request details)

Example Cockpit Meal Query:
SELECT COUNT(*) FROM flight_reports 
WHERE LeadAskedForCockpitMeal = 'Yes' 
AND Flight_Date_Time >= '2025-04-01' 
AND Flight_Date_Time < '2025-10-01'

Available Categories and Sub-Categories:

Categories:
- Catering Issues
- Star Performer Of My Flight  
- MPOS Serviceability
- Airport Issues
- Cabin Events
- Engineering Issues
- Finance
- Crew Feedback
- Services Impacted
- Customer issues
- Delay on flight
- Security issues
- Hotel Issues
- Transport Issues

Sub-Categories:
- Catering equipment not uplifted/shortage
- Star Performer Of My Flight
- MPOS Serviceability
- MAAS Issues
- Safety reporting
- Unsatisfactory Cabin Temperature
- Prebooked Snacks not uplifted
- Catering to crew - catering handover discrepency
- Other Finance Issues/Feedback
- Conduct Of Cabin Crew
- Unorganized Catering Handover
- Other Airport Issues
- Coach Issues- Cabin Crew
- Cabin Appearance items not uplifted
- Foreign Body in Catering Item
- Services Impacted
- Rude Behavior By AOCS Staff
- Quality of Perishable Item
- Boiler Unserviceable
- Bond Discrepancy
- Company Mail Process Deviation
- Grooming Of Cabin Crew
- Shortage- catering equipment & Dry store
- Unserviceable other equipments
- Aircraft Cleanliness
- Crew to Crew Handover Issues
- Coach Issues-Passenger
- Appreciation For AOCS Staff
- Crew Meal Issues
- Manifest Issues
- Late Reporting Of Cabin Crew
- Customer Issues-Service Recovery Done
- Customer Issues-Requires Escalations
- Cabin Defect
- Delay on flight
- Shortage of saleable items
- Wow Moments Created Zone Wise
- International Layover Sign In
- Child re-seating done
- All world Passport
- Shortage of perishable items
- Cabin Defects
- WHCR Issues
- Lost/found Passengers belongings
- Hotel Issues
- Seat Duplication
- Quality of Non-Perishable Item
- NPSD
- Feedback for PA system
- UNMR Process Deviation
- Fumigation can not uplifted/shortage
- Appreciation For Catering Staff
- Transport Issues
- Customer Issues - Service Recovery Done
- Customer Issues - Requires Escalations
- Appreciation For Engineering Staff
- Quality of Non- Perishable items
- Conduct Of ACM On Board
- Unruly passenger
- Quality of Perishable items
- FOREX issues
- Unserviceable Carts
- Rude Behavior By Security staff
- Catering cart not sealed properly
- Catering to crew - catering handover discrepancy
- Lost/found Passenger's belongings
- Shortage in Dry Store
- Appreciation for Security Staff
- Company Mail
- Child re-seating  done
- Forex Issue

IMPORTANT: Subcategory Selection Guidelines:
When interpreting user queries, use these specific subcategory matches:
- "cabin appearance issues/problems" → Use 'Cabin Appearance items not uplifted'
- "aircraft cleanliness issues" → Use 'Aircraft Cleanliness'
- "crew meal issues/problems" → Use 'Crew Meal Issues'
- "catering handover problems" → Use 'Catering to crew - catering handover discrepancy' or 'Unorganized Catering Handover'
- "customer service recovery" → Use 'Customer Issues-Service Recovery Done'
- "customer escalations" → Use 'Customer Issues-Requires Escalations'
- "perishable food quality" → Use 'Quality of Perishable Item' or 'Quality of Perishable items'
- "non-perishable quality" → Use 'Quality of Non-Perishable Item' or 'Quality of Non- Perishable items'
- "child reseating" → Use 'Child re-seating done' or 'Child re-seating  done'


Query Examples:
-- COCKPIT MEAL QUERIES 
SELECT COUNT(*) as cockpit_meal_requests
FROM flight_reports 
WHERE LeadAskedForCockpitMeal = 'Yes'
AND Flight_Date_Time >= '2025-04-01' 
AND Flight_Date_Time < '2025-10-01'
"""
    log.info(f"Received BigQuery request with query length: {len(query) if query else 0}")
    
    if not query or not query.strip():
        log.warning("Empty query received")
        return {"error": "No query provided"}
    
    # Basic validation - prevent obviously dangerous queries
    query_upper = query.upper().strip()
    if any(dangerous in query_upper for dangerous in ['DELETE', 'DROP', 'ALTER', 'INSERT', 'UPDATE']):
        return {"error": "Only SELECT queries are allowed for security reasons"}

    def json_serializer(obj):
        """JSON serializer for objects not serializable by default json code"""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif hasattr(obj, '__str__'):
            return str(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    try:
       
        client = bigquery.Client()
        # Configure job to add timeout and dry run option
        job_config = bigquery.QueryJobConfig()
        job_config.use_query_cache = True
        job_config.maximum_bytes_billed = 10**9  # 1GB limit to prevent expensive queries

        # Execute query
        log.debug(f"Executing BigQuery: {query[:100]}...")
        job = client.query(query, job_config=job_config)

        # Convert results to list of dictionaries
        rows = [dict(row) for row in job.result()]  # .result() waits for completion
        log.debug(f"BigQuery returned {len(rows)} rows")
        
        # Return output as JSON string with custom serializer for datetime objects
        return {"result": json.dumps(rows, indent=2, default=json_serializer)}

    except Exception as e:
        error_msg = str(e)
        log.error(f"BigQuery error: {error_msg}")

        # Return user-friendly error messages for common issues
        if "could not be authenticated" in error_msg.lower():
            return {"error": "BigQuery authentication failed. Please set GOOGLE_APPLICATION_CREDENTIALS."}
        elif "not found" in error_msg.lower():
            return {"error": "Dataset or table not found. Please check your query."}
        elif "permission denied" in error_msg.lower():
            return {"error": "Permission denied. Check BigQuery access permissions."}
        else:
            return {"error": f"BigQuery error: {error_msg}"}


@mcp.tool("bigquery_nps")
async def bigquery_run_query(query: str):
    """
bigquery_nps Tool Description

Overview:
Executes SQL queries against BigQuery NPS (Net Promoter Score) datasets for customer satisfaction analysis. Provides access to passenger feedback, experience ratings.

CRITICAL: Use this tool for ALL NPS-related queries including:
- NPS scores, ratings, and satisfaction analysis
- Customer feedback and passenger experience data  
- Promoters, Passives, Detractors analysis
- Route satisfaction and crew performance correlation
- Passenger survey responses and experience touchpoints

Keywords that indicate this tool should be used:
- "NPS", "Net Promoter Score", "satisfaction", "promoter", "detractor", "passive", "customer experience"
- "survey", "rating", "passenger feedback", "satisfaction score", "customer satisfaction"


Input/Output Specification:

Input Format:
{"query": "SELECT * FROM nps_cx_data WHERE Departure_Date > '2025-01-01'"}

Query Requirements:
- Allowed: Only SELECT statements
- Syntax: Standard BigQuery SQL

Output Format:
{
  "result": "[{\"NPS_Type\":\"Promoter\",\"FLTNBR\":\"2386\",\"DEP\":\"DEL\",\"ARR\":\"BOM\",\"NPS_Score\":9,\"Booking_experience\":5,\"Check_in_experience\":4,\"On_board_experience\":4,\"CaptainName\":\"Robert Smith\",\"CaptainIGACode\":\"IGA78901\",\"Please_share_your_reasons_for_the_rating\":\"Excellent service and on-time performance\"}]"
}


Core NPS Metrics:
- NPS_Score: Primary Net Promoter Score (0-10 scale)
- NPS_Type: Classification ("Promotors", "Passives", "Detractors")
  - **Promotors**: Highly satisfied customers who are praising the services
    - **ALWAYS use when analyzing customer appreciation, praise, or positive feedback**
  - **Passives**: Satisfied but not enthusiastic customers who are neutral about services
    - **Use for neutral or mixed feedback scenarios**
  - **Detractors**: Unsatisfied customers who might discourage others from choosing IndiGo
    - **ALWAYS use when analyzing customer complaints, negative feedback, or service issues**
- Response_Status: Survey completion status ( Started/Completed)
- Survey_Name: Survey campaign identifier (Travel Experience)


Example NPS_Type Queries:
-- Count promoters (NOT "Promotor")
SELECT COUNT(*) FROM nps_cx_data WHERE NPS_Type = 'Promotors'

IMPORTANT: NPS_Type Selection Guidelines:
When interpreting user queries about NPS categories, use these specific value matches:
- "promoter/promoters/promotor" → Use 'Promotors'
- "passive/passives" → Use 'Passives'  
- "detractor/detractors" → Use 'Detractors'

CRITICAL: Route-Based Queries:
When querying for specific routes or city pairs, ALWAYS use DEP and ARR fields separately instead of City_Pair:

CORRECT Route Query Format:
-- For DEL-BOM route queries, use DEP and ARR separately
SELECT COUNT(*) FROM nps_cx_data WHERE DEP = 'DEL' AND ARR = 'BOM'

-- NOT: WHERE City_Pair = 'DEL-BOM' (avoid this for connecting flights)

Route Query Guidelines:
- "DEL to BOM" / "DEL-BOM" → Use DEP = 'DEL' AND ARR = 'BOM'
- "Mumbai to Delhi" → Use DEP = 'BOM' AND ARR = 'DEL'  
- "flights from DEL" → Use DEP = 'DEL'
- "flights to BOM" → Use ARR = 'BOM'
- "domestic routes" → Use appropriate Indian airport codes
- "international routes" → Use international airport codes

Why use DEP/ARR instead of City_Pair:
- Handles connecting flights with multiple cities correctly
- More flexible for partial route queries (departure-only or arrival-only)


Flight Information:
- FLTNBR: IndiGo flight number (1454) (No 6E prefix)
- DEP: Departure airport codes(for eg. BOM)
- ARR: Arrival airport codes(for eg. DEL)
- City_Pair: Route designation (DEP-ARR format)
- Departure_Date/Arrival_Date : Flight scheduling information (for eg. 2025-12-14). 

**When date filtering is requested, ALWAYS use Arrival_Date column as the primary date field:**
**IMPORTANT: Do not use timezone when filtering with Arrival_Date - use plain date format (YYYY-MM-DD)**
**Examples:**
- "Show NPS data for December 14, 2025" → WHERE Arrival_Date = '2025-12-14'
- "Flights between Jan 1-15, 2025" → WHERE Arrival_Date BETWEEN '2025-01-01' AND '2025-01-15'



- Departure_Date_Time: Flight scheduling information ( for eg. 2025-12-17 06:18:00 UTC)
- Equipment_Type: Aircraft type(320,321,ATR)
- FlightDuration: Total flight time
- Start_Date_Time: when the survey started not flight start time. (for eg. 2025-12-17 06:18:00 UTC)

Passenger Details:
- Title/First_Name/Last_Name: Passenger identification
- Email/Mobile: Contact information (anonymized per privacy settings)
- Gender/DOB: Demographic information
- PNR: Passenger Name Record
- Pax_Type: Passenger category (ADT/CHD)

Experience Ratings (0-5 Scale):
- Booking_experience: Online/offline booking process satisfaction
- Pre_travel_information_experience: Pre-flight information quality
- Check_in_experience: Airport check-in process satisfaction
- Boarding_experience: Gate and boarding process satisfaction
- On_board_experience: In-flight service satisfaction
- Arrival_experience: Baggage and arrival process satisfaction

Detailed Service Quality:
Booking Process:
- Ease_of_booking_itinerary_meals_s: Booking system usability (scale 0-5)
- Required_information_available_on_websit: Website information adequacy (scale 0-5)
- Source_Of_Booking: Booking channel (Travel Agent, IndiGo Website, IndiGo App,  Others-1APA, Airport, Others-STFZ, IndiGo Call Center, Others-APTO, Others-BREC, Others-MAPP, Others-WWSK)

Check-in Process:
- CheckIn_Type: Method used (WEBCheckIn, Counter, KIOSK)
- Check_in_process_was_easy_Online_Kiosk: Process ease rating (scale 0-5)
- Time_taken_to_check_in_Counter_within15: Time efficiency rating (scale 0-5)
- Staff_efficiency_at_the_counter: Counter staff performance (scale 0-5)

Onboard Experience:
- Crew_helpfulness: Cabin crew service quality (scale 0-5)
- Cabin_cleanliness/Toilet_cleanliness: Aircraft hygiene ratings (scale 0-5)
- Clarity_of_crew_announcements: Communication effectiveness (scale 0-5)
- Snacks_and_beverage_if_experienced: Food service quality (scale 0-5)
- Quality_of_pre_booked_snacks: Pre-ordered meal satisfaction (scale 0-5)

Crew Correlation Data:
- CaptainName/Crew_Name: Captain/Crew identification
- CaptainIGACode/IGA Code/CrewIGACode_CA2/CrewIGACode_CA3/CrewIGACode_CA4: Captain/Crew member iga code (for eg. IGA92561)
- CaptainBase/Crew_Base1_LDE/Crew_Base2_CA/Crew_Base3_CA/Crew_Base4_CA: captain/crew base (for eg. DEL)

Operational Context:
- Departure_Delay: Flight delay indicator (Y/N)
- Delay_In_Flight: Delay duration ( 30 min to 2 hours, Less than 30 min , Before time, Less than 15 min, 2 hours to 3 hours, More than 3 hours, On time)
- Fast_Forward_Service: Premium service usage (TRUE/FALSE)
- Pre_Booked_Meal: (TRUE/FALSE)
- Onboard_Meal_Purchased: Meal service indicators (Yes/No)

Open-Ended Feedback:
- Please_share_your_reasons_for_the_rating: Detailed passenger comments and feedback
"""
 
    log.info(f"Received BigQuery request with query length: {len(query) if query else 0}")
    
    if not query or not query.strip():
        log.warning("Empty query received")
        return {"error": "No query provided"}
    
    # Basic validation - prevent obviously dangerous queries
    query_upper = query.upper().strip()
    if any(dangerous in query_upper for dangerous in ['DELETE', 'DROP', 'ALTER', 'INSERT', 'UPDATE']):
        return {"error": "Only SELECT queries are allowed for security reasons"}

    def json_serializer(obj):
        """JSON serializer for objects not serializable by default json code"""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif hasattr(obj, '__str__'):
            return str(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    try:
       
        client = bigquery.Client()
        # Configure job to add timeout and dry run option
        job_config = bigquery.QueryJobConfig()
        job_config.use_query_cache = True
        job_config.maximum_bytes_billed = 10**9  # 1GB limit to prevent expensive queries

        # Execute query
        log.debug(f"Executing BigQuery: {query[:100]}...")
        job = client.query(query, job_config=job_config)

        # Convert results to list of dictionaries
        rows = [dict(row) for row in job.result()]  # .result() waits for completion
        log.debug(f"BigQuery returned {len(rows)} rows")
        
        # Return output as JSON string with custom serializer for datetime objects
        return {"result": json.dumps(rows, indent=2, default=json_serializer)}

    except Exception as e:
        error_msg = str(e)
        log.error(f"BigQuery error: {error_msg}")

        # Return user-friendly error messages for common issues
        if "could not be authenticated" in error_msg.lower():
            return {"error": "BigQuery authentication failed. Please set GOOGLE_APPLICATION_CREDENTIALS."}
        elif "not found" in error_msg.lower():
            return {"error": "Dataset or table not found. Please check your query."}
        elif "permission denied" in error_msg.lower():
            return {"error": "Permission denied. Check BigQuery access permissions."}
        else:
            return {"error": f"BigQuery error: {error_msg}"}


async def _execute_sqlserver_query(query: str):
    """
    Execute a SQL query on SQL Server database.
    Internal function used by other tools.
    """
    if not query or not query.strip():
        return {"error": "No query provided"}
    
    # Basic validation - prevent obviously dangerous queries
    query_upper = query.upper().strip()
    if any(dangerous in query_upper for dangerous in [ 'DROP', 'ALTER', 'INSERT', 'UPDATE', 'EXEC', 'EXECUTE']):
        return {"error": "Only SELECT queries are allowed for security reasons"}

    def json_serializer(obj):
        """JSON serializer for objects not serializable by default json code"""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif hasattr(obj, '__str__'):
            return str(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    try:
        # Enforce service account authentication only
        if not DB_USERNAME or not DB_PASSWORD:
            return {"error": "Service account credentials missing. DB_USERNAME and DB_PASSWORD environment variables must be set."}
        
        # Build connection string - SERVICE ACCOUNT ONLY
        conn_str = (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server={DB_SERVER};"
            f"Database={DB_DATABASE};"
            f"UID={DB_USERNAME};"
            f"PWD={DB_PASSWORD};"
            "Authentication=SqlPassword;"
            "Connection Timeout=60;"
            "Encrypt=yes;"
            "TrustServerCertificate=yes;"
        )
        
        # Execute query
        with pyodbc.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                
                # Get column names and process results
                columns = [column[0] for column in cur.description] if cur.description else []
                
                if columns:
                    rows = cur.fetchall()
                    result = []
                    for row in rows:
                        row_dict = {}
                        for i, value in enumerate(row):
                            row_dict[columns[i]] = value
                        result.append(row_dict)
                    return {"result": json.dumps(result, indent=2, default=json_serializer)}
                else:
                    return {"result": json.dumps([])}

    except pyodbc.Error as e:
        error_msg = str(e)
        if "login failed" in error_msg.lower():
            return {"error": "SQL Server authentication failed. Please check credentials."}
        elif "server not found" in error_msg.lower():
            return {"error": "SQL Server not found. Please check server address and connectivity."}
        elif "database" in error_msg.lower() and "not exist" in error_msg.lower():
            return {"error": "Database not found. Please check database name."}
        else:
            return {"error": f"SQL Server error: {error_msg}"}
    
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


# ---------------- Navitaire Token Cache ----------------
_NAVITAIRE_TOKEN_CACHE: Dict[str, Any] = {"token": None, "generated_at": None}
NAVITAIRE_TOKEN_MAX_AGE_SECONDS = 15 * 60

async def get_navitaire_token(force_refresh: bool = False) -> str:
    now = time.time()
    token = _NAVITAIRE_TOKEN_CACHE.get("token")
    generated_at = _NAVITAIRE_TOKEN_CACHE.get("generated_at")
    if not force_refresh and token and generated_at and (now - generated_at) < NAVITAIRE_TOKEN_MAX_AGE_SECONDS:
        log.debug("Using cached Navitaire token")
        return token
    if not NAVITAIRE_BASE_URL:
        raise RuntimeError("NAVITAIRE_BASE_URL env var is not set")
    url = f"{NAVITAIRE_BASE_URL}/api/nsk/v2/token"
    payload = {
        "credentials": {
            "username": NAVITAIRE_USERNAME,
            "alternateIdentifier": NAVITAIRE_ALT_ID,
            "password": NAVITAIRE_PASSWORD,
            "domain": NAVITAIRE_DOMAIN,
            "channelType": int(NAVITAIRE_CHANNEL_TYPE) if NAVITAIRE_CHANNEL_TYPE and NAVITAIRE_CHANNEL_TYPE.isdigit() else NAVITAIRE_CHANNEL_TYPE,
            "locationCode": NAVITAIRE_LOCATION_CODE,
            "organizationCode": NAVITAIRE_ORG_CODE,
            "loginRole": NAVITAIRE_LOGIN_ROLE,
        },
        "applicationName": NAVITAIRE_APPLICATION_NAME,
    }
    log.debug("Requesting new Navitaire token")
    log.debug(f"Navitaire token request payload: {json.dumps(payload, indent=2)}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Accept": "application/json"}
            if NAV_USER_KEY:
                headers["user_key"] = NAV_USER_KEY
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() or {}
            new_token = (data.get("data") or {}).get("token") or data.get("token") or data.get("access_token")
            if not new_token:
                raise RuntimeError("Navitaire token not found in response")
            _NAVITAIRE_TOKEN_CACHE["token"] = new_token
            _NAVITAIRE_TOKEN_CACHE["generated_at"] = now
            log.debug("Navitaire token successfully retrieved and cached")
            return new_token
    except Exception:
        log.exception("Failed to fetch Navitaire token")
        raise

def decrypt_without_hash(cipher_text_base64: str, secret_key: str) -> str:
    """
    Decrypt a base64-encoded AES-CBC ciphertext using PKCS7 padding, without hashing the key.
    """
    key_bytes = secret_key.encode("utf-8")
    encrypted_blob = base64.b64decode(cipher_text_base64)
    iv = encrypted_blob[:16]
    encrypted_data = encrypted_blob[16:]

    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(encrypted_data) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    plaintext_bytes = unpadder.update(padded_plaintext) + unpadder.finalize()

    return plaintext_bytes.decode("utf-8")

def _aims_base_headers() -> dict:
    h = {
        "user_key": USER_KEY
        }
    log.debug(f"Base headers: {h}")
    return h


def _aims_auth_headers(token: dict | str) -> dict:
    log.debug(f"Building auth headers with token: {token}")
    h = _aims_base_headers()
    # If token is a dict, extract the string
    if isinstance(token, dict):
        token_str = token.get("access_token") or token.get("token")
    else:
        token_str = token
    h["Authorization"] = f"Bearer {token_str}"
    log.debug(f"Auth headers: {h}")
    return h

_AIMS_TOKEN_CACHE = {"value": None, "expires_at": 0.0}
log.debug("Initialized AIMS token cache")

async def _aims_fetch_token() -> str:
    now = time.time()
    log.debug(f"Current time is {now}")
    log.debug("Entered get_aims_token")

    if _AIMS_TOKEN_CACHE["value"] and _AIMS_TOKEN_CACHE["expires_at"] > now + 60:
        log.debug("Using cached AIMS token")
        return _AIMS_TOKEN_CACHE["value"]

    log.debug(f"Fetching AIMS token from URL:")
    payload = {
             "clientId": AIMS_CLIENT_ID
            }
    headers = {
        "user_key": USER_KEY
    }
    log.debug(f"Token request payload:\n{json.dumps(payload, indent=4)}")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            log.debug("Posting AIMS token request")
            resp = await client.post(AIMS_BASE + "/api/v1/token/get-token", headers=headers, json=payload)
            log.debug(f"Response status: {resp.status_code}")
            resp.raise_for_status()
            data = resp.json() or {}
            log.debug(f"Response JSON: {data}")
            token = (data.get("data") or {}).get("token") or data.get("token")
            ttl_minutes = (data.get("data") or {}).get("idleTimeoutInMinutes", 55)
            if not token:
                log.error(f"Token missing in response: {data}")
                raise RuntimeError("AIMS token not found in response")
            log.debug("AIMS token successfully retrieved")
            _AIMS_TOKEN_CACHE["value"] = token
            _AIMS_TOKEN_CACHE["expires_at"] = now + ttl_minutes*60  # 55 minutes = 3300 seconds
            return token
    except Exception as e:
        log.exception("Error fetching AIMS token", e)
        return ""
async def _get_qualification_by_iga(token: str, startDateTime: str, endDateTime: str, IGAcode: str = None, Base: str = None, Position: str = None, DocType: str = None) -> Dict[str, Any]:
    url = f"{AIMS_BASE}/api/v1/crew-qualification"
    # Build params based on provided arguments
    params = {
        "startDateTime": startDateTime,
        "endDateTime": endDateTime
    }
    if IGAcode:
        params["IGAcode"] = IGAcode
    if Base:
        params["Base"] = Base
    if Position:
        params["Position"] = Position
    if DocType:
        params["DocType"] = DocType

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            log.debug("Requesting crew qualification details")
            resp = await client.get(url, headers=_aims_auth_headers(token), params=params)
            log.debug(f"Initial response status: {resp.status_code}")
            if resp.status_code in (401, 403):
                log.warning("Auth failed, retrying with new token")
                token = await _aims_fetch_token()
                log.debug(f"New token fetched: {token}...")
                resp = await client.get(url, headers=_aims_auth_headers(token), params=params)
                log.debug(f"Retry response status: {resp.status_code}")
            resp.raise_for_status()
            json_response = resp.json()
            log.debug("Received qualification details response")
            data = json_response
            log.debug("Qualification details successfully retrieved")
            return data
    except Exception as e:
        log.exception(f"Error fetching Qualification details in AIMS")
        raise
async def _get_crew_data_by_iga(token: str, iga_code: str) -> Dict[str,Any]:
    url = f"{AIMS_BASE}/api/v1/crew-basic-info"
    params = {"IGA": iga_code}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            log.debug("GET crew-basic-info with params=%s", params)
            resp = await client.get(url, headers=_aims_auth_headers(token), params=params)
            log.debug("Response status=%s", resp.status_code)
            if resp.status_code in (401, 403):
                log.warning("AIMS auth failed (status %s), refreshing token and retrying once", resp.status_code)
                token = await _aims_fetch_token()
                resp = await client.get(url, headers=_aims_auth_headers(token), params=params)
                log.debug("Retry response status=%s", resp.status_code)
            if resp.status_code == 404:
                raise RuntimeError(f"crew-basic-info not found for iga_code={iga_code} (404)")
            resp.raise_for_status()
            json_response = resp.json() or {}
            log.debug("Received crew basic info response")
            # ...existing code...
            data_field = json_response.get("data")
            # ...existing code...
            data_field["input_iga_code"] = iga_code
            if CREW_DECRYPTION_KEY:
                log.debug("Decrypting crew basic info data fields")
                decrypted_data = {}
                for k, v in data_field.items():
                    if isinstance(v, str) and v and is_base64(v):
                        try:
                            decrypted_data[k] = decrypt_without_hash(v, CREW_DECRYPTION_KEY)
                        except Exception as dec_ex:
                            log.error("Decryption failed for key %s: %s", k, dec_ex)
                            decrypted_data[k] = v
                    else:
                        decrypted_data[k] = v
                json_response["data"] = decrypted_data
            return json_response
    except Exception as ex:
        raise RuntimeError(f"Failed to fetch crew basic info for iga_code={iga_code}: {ex}") from ex

async def _get_crew_roster_data(token: str, startDateTime: str, endDateTime: str, IGA: str = None, fltNbr: str = None) -> Dict[str,Any]:
    url = f"{AIMS_BASE}/api/v1/crew-roster/cob"
    params = {
        "startDateTime": startDateTime,
        "endDateTime": endDateTime
    }
    if IGA:
        params["IGA"] = IGA
    if fltNbr:
        params["fltNbr"] = fltNbr
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            log.debug("Requesting crew roster details")
            resp = await client.get(url, headers=_aims_auth_headers(token), params=params)
            log.debug(f"Initial response status: {resp.status_code}")
            if resp.status_code in (401, 403):
                log.warning("Auth failed, retrying with new token")
                token = await _aims_fetch_token()
                log.debug(f"New token fetched: {token}...")
                resp = await client.get(url, headers=_aims_auth_headers(token), params=params)
                log.debug(f"Retry response status: {resp.status_code}")
            resp.raise_for_status()
            json_response = resp.json()
            log.debug("Received crew roster response")
            # Return raw JSON (no decryption here)
            data = json_response
            log.debug("Crew roster details successfully retrieved")
            return data
    except Exception as e:
        log.exception(f"Error fetching crew roseter details in AIMS")
        raise

# ----------------AIMS CUSTOM TOOL -----------------

def is_base64(s):
    if not isinstance(s, str) or not s:
        return False
    # Standard base64 strings are multiples of 4 and only contain valid chars
    if len(s) % 4 != 0:
        return False
    try:
        base64.b64decode(s, validate=True)
        return True
    except Exception:
        return False

# CREW INFO BY IGA (Refactored)
from tools import flight_delay_analyzer
from tools.crew_info import CrewInfoFetcher
crew_info_fetcher = CrewInfoFetcher(_aims_fetch_token, _get_crew_data_by_iga)

@mcp.tool("crew_info_by_iga")
async def get_crew_info(iga_code: str):
    """
    Fetch crew information for a given IGA code from AIMS (crew info database).

    Sample Input (single line):
        {"iga_code": "23330"} # str

    Sample Output (single line):
        {"data": {"id": "23330", "name": "John Doe", "email": "john.doe@airline.in", "phone1Number": "9876543210", "kinDetails": {...}, "marStatus": 1, "passportDetails": {...}, "Address": {...}, "lcneDetails": {...}, "contract": "Full-time", "birthPlace": "Kolkata", "base": "BOM", "designation": "Captain", "physicalInfo": {...}, "doj": "2016-10-27", "dob": "1981-09-22", "sex": "M"}}

    Output Field Summary:
        - data: Crew member details
            - id: Crew IGA code
            - name: Full name
            - email: Primary email address
            - phone1Number: Primary contact number
            - kinDetails: Next of kin information
            - marStatus: Marital status
            - passportDetails: Passport information
            - Address: Address details
            - lcneDetails: License details
            - contract: Employment contract type
            - birthPlace: Place of birth
            - base: Crew base
            - designation: Crew designation/role
            - physicalInfo: Physical attributes
            - doj: Date of joining
            - dob: Date of birth
            - sex: Gender

    Error Handling:
        - Returns error details if IGA code is missing or invalid.
        - If no crew data is found, returns an empty object.
    """
    return await crew_info_fetcher.get_crew_info(iga_code)
    
# CREW ROSTER INFO (Refactored)
from tools.crew_roster import CrewRosterFetcher
crew_roster_fetcher = CrewRosterFetcher(_aims_fetch_token, _get_crew_roster_data)

@mcp.tool("crew_roster_info")
async def get_crew_roster_info(startDateTime: str = None, endDateTime: str = None, IGA: str = None, fltNbr: str = None):
    """
    Fetch crew roster data from AIMS (crew info database) for a given date range, filtered by IGA code or flight number.

    Sample Input (single line):
        Input date is in UTC format.
        {"startDateTime": "2025-08-01T00:00", "endDateTime": "2025-08-31T23:59", "IGA": "23330"} # str
        or {"startDateTime": "2025-08-01T00:00", "endDateTime": "2025-08-31T23:59", "fltNbr": "123"} # str (flight no. does not have 6E prefix)

    Sample Output (single line):
        {"data": [{"empno": "23330", "leg_Day": "2025-07-31T00:00:00", "croute": "", "pos": 1, "designation": 1, "grddutybeg": 0, "grddutyend": 0, "asgCat": "", "recType": 0, "value1": 0, "value2": 0, "facilidx": 0, "firstName": "John", "middleName": "A", "lastName": "Doe", "senior": 34, "dep": "DEL", "arr": "BOM", "flt": "6E123", "cwbase": "BOM", "actype": "321", "actdep": "2025-07-31T11:10:00", "actarr": "2025-07-31T13:24:00", "std": "2025-07-31T11:15:00", "sta": "2025-07-31T13:30:00", "dutyType": "Flight", "traineeCode": "", "isACM": "NO", "boardingType": "", "email": "john.doe@airline.in", "phoneNumber": "9876543210"}]}

    Output Field Summary:
    NOTE - All fields related to time are specified in UTC, not IST.
        - data: List of crew roster records 
            - empno: Employee number
            - leg_Day: Leg date/time (ISO format)
            - croute: Crew route
            - pos: Position code
            - designation: Designation code
            - grddutybeg: Ground duty begin
            - grddutyend: Ground duty end
            - asgCat: Assignment category
            - recType: Record type
            - value1, value2: Additional values
            - facilidx: Facility index
            - firstName, middleName, lastName: Crew member names
            - senior: Seniority
            - dep, arr: Departure/arrival airport codes
            - flt: Flight number
            - cwbase: Crew base
            - actype: Aircraft type
            - actdep, actarr: Actual departure/arrival times
            - std, sta: Scheduled departure/arrival times
            - dutyType: Duty type
            - traineeCode: Trainee code
            - isACM: ACM status
            - boardingType: Boarding type
            - email: Crew email
            - phoneNumber: Crew phone number

    Error Handling:
        - Returns error details if mandatory inputs are missing or invalid.
        - If no roster data is found, returns an empty list.


    Usage Guidance:
        - Provide both start and end dates in ISO format.
        - Specify either 'IGA' or 'fltNbr' for filtering.
    """
    return await crew_roster_fetcher.get_crew_roster_info(startDateTime, endDateTime, IGA, fltNbr)
        

# CREW QUALIFICATION INFO (Refactored)
from tools.crew_qualification import CrewQualificationFetcher

crew_qualification_fetcher = CrewQualificationFetcher(_aims_fetch_token, _get_qualification_by_iga)

@mcp.tool("qualification_info_by_iga")
async def get_qualification_info(startDateTime: str, endDateTime: str, IGAcode: str = None, Base: str = None, Position: str = None, DocType: str = None):
    """
    Fetch the Qualifications data from AIMS (crew info database).
    Case 1: IGA, startDateTime, endDateTime provided by client
    Case 2: Base, Position, startDateTime, endDateTime provided by client 
    If the startDateTime and endDateTime is not specified, Kindly pass/use today's date as input
    DocType is optional

    Sample Input (single line):
        Input date is in UTC format.
        {"IGAcode": "23330", "startDateTime": "2025-08-01T00:00", "endDateTime": "2025-08-31T23:59"} # str
        or {"Base": "BOM", "Position": "FO", "startDateTime": "2025-08-01T00:00", "endDateTime": "2025-08-31T23:59"} # str

    Sample Output (single line):
        {"crewQualifications": [{"crewId": "23330", "base": "BOM", "position": "FO", "activeDocuments": [...], "expiredDocuments": [...], "aircraftQualifications": [...]}]}

    Output Field Summary:
        - crewQualifications: List of crew qualification records
            - crewId: Crew identifier
            - base: Crew base
            - position: Crew position
            - activeDocuments: List of active qualification documents (fields: qualTypeId, qualTypeDescription, qualValidTo, active, issueCountryCode, empDocCode, empDocInfo, warningDaysBefore, label, defaultLength, qualUpdatedFrom, qualUpdatedTo, checkDate, countryCodeDependancy, base, position)
            - expiredDocuments: List of expired qualification documents (same fields as activeDocuments, do not repeat)
            - aircraftQualifications: List of aircraft qualifications (fields: qualification, aircraftType, rank, tranqual, base, activBeg, activEnd, code)

    Error Handling:
        - Returns error details if mandatory inputs (dates, IGA or Base/Position) are missing or invalid.
        - If no qualification data is found, returns an empty list.

    Usage Guidance:
        - Provide startDateTime and endDateTime in ISO format.
        - Specify either IGAcode or both Base and Position for filtering.
        - DocType is optional and can be used to filter by document type.
    """
    return await crew_qualification_fetcher.get_qualification_info(startDateTime, endDateTime, IGAcode, Base, Position, DocType)
    
#JOC API's
_JOC_TOKEN_CACHE = {"value": None, "expires_at": 0.0}
log.debug("Initialized JOC token cache")

def _get_joc_access_token():
    now = time.time()
    if _JOC_TOKEN_CACHE["value"] and _JOC_TOKEN_CACHE["expires_at"] > now + 60:
        log.debug("Using cached JOC token")
        return _JOC_TOKEN_CACHE["value"]

    try:
        url = JOC_TOKEN_ENDPOINT
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        payload = {
            "client_id": JOC_CLIENT_ID,
            "client_secret": JOC_CLIENT_SECRET,
            "scope": JOC_SCOPE,
            "grant_type": "client_credentials"
        }
        with httpx.Client(timeout=20) as client:
            response = client.post(url, headers=headers, data=payload)
            if response.status_code >= 400:
                log.error(f"JOC token request failed with status {response.status_code}: {response.text}")
            response.raise_for_status()
            data = response.json()
            
            access_token = data.get('access_token')
            if not access_token:
                raise RuntimeError("JOC access_token not found in response")

            # Assuming token expires in 1 hour (3600 seconds) as not specified in response
            _JOC_TOKEN_CACHE["value"] = access_token
            _JOC_TOKEN_CACHE["expires_at"] = now + 3540  # Cache for 59 minutes
            
            log.debug("JOC token successfully retrieved")
            return access_token

    except Exception as e:
        log.exception('Failed to get JOC access token')
        raise


# FLIGHT DATA BY JOC (Refactored)
from tools.flight_data import FlightScheduleFetcher
from tools.missing_flight_reports import MissingFlightReportsFetcher
from tools.flight_delay_analyzer import FlightDelayAnalyzer

flight_data = FlightScheduleFetcher(_get_joc_access_token)
missing_flight_reports_fetcher = MissingFlightReportsFetcher(_get_joc_access_token)
flight_delay_analyzer = FlightDelayAnalyzer(_get_joc_access_token)


@mcp.tool("flight_data_by_joc")
def get_flight_data(startDateTime: str = None, endDateTime: str = None, startStation: str = None, flightNumber: int = None, endStation: str = None):
    """
    Fetch flight data from JOC(Jeppsen Operations Control) for the specified parameters.

    Sample Input (single line):
        Input date is in UTC format.
        {"startDateTime": "2026-01-16T00:00", "endDateTime": "2026-01-16T23:59", "startStation": "UDR"} #str

    Sample Output (single line):
        {"success": true, "count": 1, "data": [{"id": "6E#2026-01-16#2163#UDR#DEL", "flightNumber": 2163, "startTimeOffset": "05:30", "endTimeOffset": "05:30", "startStation": "UDR", "endStation": "DEL", "flightStatus": "", "scheduledStartTime": "2026-01-16T03:10:00", "scheduledEndTime": "2026-01-16T04:40:00", "operation": {"estimatedTimes": {...}, "actualTimes": {...}}, "handling": {"serviceType": "J"}, "equipment": {"aircraft": {"registration": "VTIAX", "type": "32M"}, "plannedaircraftTypeICAO": "A320"}}], "pages_processed": 1}

    Output Field Summary:
        - success: Boolean indicating if flight data is available
        - count: Number of flight records returned
        - data: List of flight records
            - id: Unique flight identifier
            - flightNumber: Numeric flight number
            - startTimeOffset: Scheduled start time offset
            - startStation: Departure airport code
            - endStation: Arrival airport code
            - flightStatus: Status of the flight
            - scheduledStartTime: Scheduled departure time (ISO format)
            - scheduledEndTime: Scheduled arrival time (ISO format)
            - operation: Operation details (fields: estimatedTimes, actualTimes)
            - handling: Handling details (fields: serviceType)
            - equipment: Equipment details (fields: aircraft (registration, type), plannedaircraftTypeICAO)
        - errorMessage: Error message if any
        - currentPage: Current page number
        - currentPageElements: Number of elements on current page
        - totalPages: Total number of pages
        - totalPagesElements: Total number of elements

    Parameters:
        - startDateTime: Optional start date/time (defaults to current date/time if not provided)
        - endDateTime: Optional end date/time (defaults to current date 23:59 if not provided) 
        - startStation: Optional departure airport code (e.g., "UDR")
        - flightNumber: Optional flight number (e.g., 2163)
        - endStation: Optional arrival airport code (e.g., "DEL")

    Output:
        - Returns direct API JSON response
        - No aggregation or processing of multiple pages

    Error Handling:
        - Returns error details if API call fails
        - Network or authentication errors are logged and returned in the response

    Usage Guidance:
        - All parameters are optional
        - startDateTime/endDateTime default to current date if not provided
        - Returns raw API response for maximum flexibility
    """
    return flight_data.get_flight_data(startDateTime, endDateTime, startStation, flightNumber, endStation)

@mcp.tool("missing_flight_reports")
def get_missing_flight_reports(startDateTime: str = None, endDateTime: str = None, startStation: str = None):
    """
    missing_flight_reports Tool - Optimized Description

    Overview
    --------
    Identifies flights scheduled in JOC but missing flight reports in BigQuery through automated cross-system data validation. Performs real-time comparison between operational schedules and completed flight reports to ensure regulatory compliance and documentation completeness.

    Core Functionality
    ------------------
    - Primary Use: Cross-system validation for flight report compliance
    - Data Sources: JOC Flight Time API + BigQuery flight reports table
    - Authentication: Dual system (JOC OAuth + Google Cloud Service Account)
    - Processing: Real-time comparison with flight number normalization

    Input/Output Specification
    --------------------------

    Input Format:
    Input date is in UTC format.
    {
      "startDateTime": "2026-01-12",
      "endDateTime": "2026-01-12",
      "startStation": "DEL"
    }

    Input Parameters:
    - startDateTime: Start date (YYYY-MM-DD format, optional - defaults to today)
    - endDateTime: End date (YYYY-MM-DD format, optional - defaults to today)
    - startStation: Departure airport code (MANDATORY - 3-letter IATA code)

    Output Format:
    {
      "success": true,
      "total_scheduled_flights": 50,
      "total_reported_flights": 45,
      "missing_flights_count": 5,
      "missing_flights": [
        {
          "flightNumber": "6350",
          "startStation": "DEL",
          "endStation": "LKO",
          "scheduledStartTime": "2026-01-12T00:00:00",
          "scheduledEndTime": "2026-01-12T01:05:00",
          "aircraft_registration": "VTICX",
          "id": "6E#2026-01-12#6350#DEL#LKO"
        }
      ],
      "date_range": {
        "startDateTime": "2026-01-12",
        "endDateTime": "2026-01-12", 
        "startStation": "DEL"
      },
      "processing_details": {
        "joc_query_time_ms": 1250,
        "bigquery_time_ms": 2100,
        "total_processing_time_ms": 3400
      }
    }

    Key Data Fields
    ---------------

    Summary Statistics:
    - success: Boolean operation completion status
    - total_scheduled_flights: JOC scheduled flight count
    - total_reported_flights: BigQuery completed report count
    - missing_flights_count: Gap between scheduled and reported
    - coverage_percentage: Reporting completeness percentage

    Missing Flight Details:
    - flightNumber: Cleaned numeric flight number (6E prefix removed)
    - startStation/endStation: Departure/arrival airport codes
    - scheduledStartTime/scheduledEndTime: Planned flight times (ISO format)
    - aircraft_registration: Aircraft tail number
    - id: Unique JOC flight identifier

    Processing Metadata:
    - date_range: Echo of input parameters
    - processing_details: Performance timing breakdown

    Data Comparison Methodology
    ---------------------------

    Cross-System Validation Process:
    1. JOC Data Retrieval: Query scheduled flights from Flight Time API
    2. BigQuery Query: Extract completed reports from fr_tagging table
    3. Flight Number Normalization: Remove prefixes for accurate matching
    4. Cross-Reference: Compare flight identifiers across systems
    5. Gap Analysis: Identify JOC flights without BigQuery reports

    Matching Criteria:
    - Primary Key: Flight number + departure date + departure station
    - Normalization: Automatic "6E-" prefix removal for standardization
    - Validation: Aircraft registration and timing confirmation
    - Quality Assurance: Data completeness validation before comparison

    Technical Details
    -----------------

    Dual Authentication System:
    - JOC Authentication: OAuth 2.0 Client Credentials (59-minute cache)
    - BigQuery Authentication: Service Account JSON credentials
    - Security: Separate credential management with isolated error handling
    - Monitoring: All API calls logged for security and performance tracking

    Performance Characteristics:
    - Simple Queries (single day/station): 3-8 seconds
    - Complex Queries (multiple days/busy stations): 10-30 seconds
    - Peak Load (high-traffic periods): 30-60 seconds
    - Optimization: Parallel processing where possible with connection pooling

    Error Recovery:
    - JOC Failures: Token refresh, retry logic, timeout handling
    - BigQuery Failures: Authentication retry, query optimization
    - Network Issues: Connection timeout and retry mechanisms
    - Partial Failures: Graceful degradation with error reporting

    Error Handling
    --------------

    Input Validation Errors:
    {
      "success": false,
      "error": "missing_mandatory_parameter",
      "message": "startStation parameter is required"
    }

    System-Specific Errors:
    - JOC Errors: Authentication, timeout, server errors
    - BigQuery Errors: Access denied, dataset not found, query timeout
    - Comparison Errors: Data format mismatches, processing failures
    - Network Errors: Connectivity issues, request timeouts

    Use Cases & Applications
    ------------------------

    Primary Use Cases:
    1. Compliance Monitoring: Detect flights with missing mandatory reports
    2. Quality Assurance: Validate flight documentation completeness
    3. Gap Analysis: Identify discrepancies between scheduled and reported flights
    4. Station-Specific Queries: Check compliance for specific departure airports

    When to Use This Tool:
    - Need to verify flight report completeness for a specific date/station
    - Investigating missing documentation for regulatory purposes
    - Cross-validating data between JOC scheduling and BigQuery reports
    - Checking compliance status before audits or reviews

    System Dependencies
    -------------------
    - Network: HTTPS connectivity to JOC and Google Cloud APIs
    - Packages: httpx, google-cloud-bigquery, google-auth
    - Credentials: Valid authentication for both JOC and BigQuery systems
    - Permissions: Read access to flight schedules and reports

    Best Practices
    --------------

    Query Guidelines:
    - Always specify startStation: Required parameter for performance and data filtering
    - Use specific date ranges: Single day queries perform better than broad date ranges
    - Handle timeouts gracefully: Complex queries may take 30-60 seconds

    Parameter Selection:
    - Station codes: Use 3-letter IATA codes (DEL, BOM, BLR, etc.)
    - Date format: YYYY-MM-DD format required
    - Default behavior: Omitted dates default to current date

    Error Handling:
    - Authentication failures: Both JOC and BigQuery credentials must be valid
    - Network timeouts: Retry logic built-in, but queries may still timeout
    - Invalid parameters: Proper validation prevents most input errors

    This tool provides essential operational oversight for regulatory compliance through automated cross-system validation, ensuring comprehensive flight reporting and enabling proactive identification of documentation gaps.
    """
    return missing_flight_reports_fetcher.get_missing_flight_reports(startDateTime, endDateTime, startStation)
@mcp.tool("flight_delay_analysis")
def get_flight_delay_analysis(
    startDateTime: str = None, 
    endDateTime: str = None, 
    page: int = 1, 
    size: int = 3000, 
    flight_count: int = 10
):
    """
    Analyze flight delays for a given time period using JOC flight data.

    Sample Input (single line):
        Input date is in UTC format.
        {"startDateTime": "2026-01-12T00:00", "endDateTime": "2026-01-12T23:59", "page": 1, "size": 3000, "flight_count": 10}

    Sample Output:
        {
          "flight_level_counts_of_delays": {
            "total_flights": 3000,
            "delayed_flights": 450,
            "on_time_flights": 2550
          },
          "top_flights_summary": [
            {
              "flight_number": "6E882",
              "start_station": "DEL",
              "end_station": "MAA", 
              "sector": "DEL-MAA",
              "scheduled_datetime": "2026-01-11T14:00:00",
              "actual_datetime": "2026-01-11T15:00:00",
              "delay_minutes": 60
            }
          ],
          "top_sectors_summary": [
            {
              "total_flights_in_sector": 12,
              "delayed_flights_in_sector": 2,
              "on_time_flights_in_sector": 10,
              "sector": "DEL-MAA",
              "average_delay_minutes": 60.0,
              "top_15_delayed_flights": "6E882"
            }
          ],
          "top_flights_raw_data": [...]
        }

    Parameters:
        - startDateTime: Start date/time in format "YYYY-MM-DDTHH:MM" (defaults to current date/time)
        - endDateTime: End date/time in format "YYYY-MM-DDTHH:MM" (defaults to current date 23:59)
        - page: Page number for pagination (default: 1)
        - size: Number of records per page (default: 3000)
        - flight_count: Number of top delayed flights to return (default: 10)

    Output Field Summary:
        - flight_level_counts_of_delays: Overall delay statistics
            - total_flights: Total number of flights analyzed
            - delayed_flights: Number of flights with delays
            - on_time_flights: Number of flights on time or early
        - top_flights_summary: Summary of most delayed flights
            - flight_number: Flight number with 6E prefix
            - start_station: Departure airport code
            - end_station: Arrival airport code
            - sector: Route in DEP-ARR format
            - scheduled_datetime: Scheduled departure time
            - actual_datetime: Actual takeoff time
            - delay_minutes: Delay in minutes
        - top_sectors_summary: Summary of sectors with most delays
            - total_flights_in_sector: Total flights in this route
            - delayed_flights_in_sector: Delayed flights in this route
            - on_time_flights_in_sector: On-time flights in this route
            - sector: Route in DEP-ARR format
            - average_delay_minutes: Average delay for this route
            - top_15_delayed_flights: Flight numbers of most delayed flights (max 15)
        - top_flights_raw_data: Complete raw data for top delayed flights

    Delay Calculation:
        - Compares scheduledStartTime with operation.actualTimes.takeoffTime
        - Positive values indicate delays, negative values indicate early departures
        - Only flights with positive delay values are considered "delayed"

    Error Handling:
        - Returns error details if JOC API call fails
        - Handles missing time data by treating flights as on-time
        - Invalid date formats or API connectivity issues are logged and returned

    Usage Guidance:
        - Leave startDateTime/endDateTime empty to analyze current day
        - Increase flight_count to get more top delayed flights
        - Use page and size parameters for handling large datasets
        - Results are sorted by delay time (highest delay first)
    """
    return flight_delay_analyzer.get_flight_delay_analysis(startDateTime, endDateTime, page, size, flight_count)

    
# ---------------- Navitaire Tools -----------------

@mcp.tool("CDP_data_by_nav")
async def navitaire_manifest_leg_key(Origin: str, Destination: str, CarrierCode: str, BeginDate: str, Identifier: str, FlightType: str):
    """
    Fetch manifest leg key, then get manifest, then get booking details for each record locator.
    """
    log.debug("Entered navitaire_manifest_leg_key tool")
    log.debug(f"Input parameters -> Origin: {Origin}, Destination: {Destination}, CarrierCode: {CarrierCode}, BeginDate: {BeginDate}, Identifier: {Identifier}, FlightType: {FlightType}")

    required_params = [Origin, Destination, CarrierCode, BeginDate, Identifier, FlightType]
    if any(v is None or str(v).strip() == "" for v in required_params):
        log.error("Missing required parameters")
        raise ValueError("All parameters (Origin, Destination, CarrierCode, BeginDate, Identifier, FlightType) must be provided and non-empty")

    if not all([NAVITAIRE_BASE_URL, NAVITAIRE_MANIFEST_LEGKEY_ENDPOINT]):
        log.error("Missing Navitaire configuration")
        raise RuntimeError("NAVITAIRE_BASE_URL and NAVITAIRE_MANIFEST_LEGKEY_ENDPOINT must be set")

    try:
        token = await get_navitaire_token()
        log.debug("Navitaire token retrieved")

        # Step 1: Get Manifest Leg Key
        leg_key_data = await _get_manifest_leg_key(token, Origin, Destination, CarrierCode, BeginDate, Identifier, FlightType)
        if not leg_key_data.get("success"):
            return leg_key_data

        # Extract legKey
        try:
            leg_key = leg_key_data['data']['data'][0]['journeys'][0]['segments'][0]['legs'][0]['legKey']
        except (KeyError, IndexError) as e:
            log.error(f"Could not extract legKey from response: {e}. Full leg_key_data: {json.dumps(leg_key_data, indent=2)}")
            return {"success": False, "error": "leg_key_not_found", "message": "Could not find legKey in the response from the manifest leg key endpoint."}

        # Step 2: Fetch Record Locators
        record_locators_response = await _fetch_record_locators(token, leg_key)
        if not record_locators_response.get("success"):
            return record_locators_response

        record_locators = record_locators_response['data']

        # Step 3: Get Booking Details for each RecordLocator
        booking_details = []
        email_phone_list = []
        for rl in record_locators:
            details = await _get_booking_by_rl(token, rl)
            booking_details.append(details)
            # Extract email and phone from booking details
            if details.get("success") and details.get("data"):
                data = details["data"].get("data", {})
                contacts = data.get("contacts", {})
                for contact in contacts.values():
                    email = contact.get("emailAddress")
                    phone_numbers = contact.get("phoneNumbers", [])
                    for phone_obj in phone_numbers:
                        phone = phone_obj.get("number")
                        # Remove '+91' prefix if present
                        if phone and phone.startswith("+91"):
                            phone = phone.replace("+91", "").strip()
                        if email and phone:
                            email_phone_list.append({"email": email, "phone": phone})

        # Step  4: Call external API for each email/phone
        external_api_url = os.getenv("PRISM_EDH_URL")
        external_api_key = os.getenv("EDH_API_USER_KEY")
        external_api_responses = []
        import requests
        for ep in email_phone_list:
            params = {"email": ep["email"], "phone": ep["phone"]}
            headers = {
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'User_Key': external_api_key
            }
            try:
                resp = requests.request("GET", external_api_url, headers=headers, params=params, verify=False)
                external_api_responses.append({
                    "email": ep["email"],
                    "phone": ep["phone"],
                    "status_code": resp.status_code,
                    "response": resp.text
                })
            except Exception as ex:
                external_api_responses.append({
                    "email": ep["email"],
                    "phone": ep["phone"],
                    "error": str(ex)
                })

        return external_api_responses

    except Exception as e:
        log.exception("Unexpected error in navitaire_manifest_leg_key tool")
        return {"success": False, "error": "unexpected_error", "message": f"An unexpected error occurred: {str(e)}"}

async def _get_manifest_leg_key(token: str, Origin: str, Destination: str, CarrierCode: str, BeginDate: str, Identifier: str, FlightType: str):
    base_url = NAVITAIRE_BASE_URL.rstrip('/')
    endpoint = NAVITAIRE_MANIFEST_LEGKEY_ENDPOINT.strip('/')
    url = f"{base_url}/{endpoint}"
    params = {
        "Origin": Origin, "Destination": Destination, "CarrierCode": CarrierCode,
        "BeginDate": BeginDate, "Identifier": Identifier, "FlightType": FlightType
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if NAV_USER_KEY:
        headers["user_key"] = NAV_USER_KEY

    log.debug(f"Requesting manifest leg key from url")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code in (401, 403):
            token = await get_navitaire_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.get(url, headers=headers, params=params)
        
        if resp.status_code >= 400:
            return {"success": False, "error": "api_error", "status_code": resp.status_code, "message": "Failed to get manifest leg key"}
        
        return {"success": True, "data": resp.json()}

async def _get_manifest(token: str, leg_key: str):
    base_url = NAVITAIRE_BASE_URL.rstrip('/')
    endpoint = NAVITAIRE_GET_MANIFEST_ENDPOINT
    if not endpoint:
        log.warning("NAVITAIRE_GET_MANIFEST_ENDPOINT is not set in .env, using a default value.")
        endpoint = "api/nsk/v1/manifests"
    
    url = f"{base_url}/{endpoint.strip('/')}/{leg_key}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if NAV_USER_KEY:
        headers["user_key"] = NAV_USER_KEY

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code in (401, 403):
            token = await get_navitaire_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.get(url, headers=headers)

        if resp.status_code >= 400:
            log.error(f"Manifest API returned status {resp.status_code}. Response: {resp.text}")
            return {"success": False, "error": "api_error", "status_code": resp.status_code, "message": "Failed to get manifest"}
        
        try:
            json_data = resp.json()
        except json.JSONDecodeError as e:
            log.error(f"Failed to decode JSON from manifest API response: {e}. Raw response: {resp.text}")
            return {"success": False, "error": "json_decode_error", "message": f"Failed to decode JSON from manifest API. Please check the upstream API response for malformed JSON. Error: {e}", "raw_response": resp.text}
        
        return {"success": True, "data": json_data}

async def _fetch_record_locators(token: str, leg_key: str) -> Dict[str, Any]:
    manifest_data = await _get_manifest(token, leg_key)
    if not manifest_data.get("success"):
        return manifest_data

    passengers_data = manifest_data['data']['data']['passengers']

    if not passengers_data:
        log.error("No 'passengers' data found in manifest response or it's empty in _fetch_record_locators.")
        return {"success": False, "error": "record_locators_not_found", "message": "No 'passengers' data found in the manifest response."}

    record_locators = []
    for p in passengers_data:
        if isinstance(p, dict) and 'recordLocator' in p and p['recordLocator']:
            record_locators.append(p['recordLocator'])
    
    if not record_locators:
        log.error("No valid RecordLocators found in the 'passengers' data in _fetch_record_locators.")
        return {"success": False, "error": "record_locators_not_found", "message": "No valid RecordLocators found in the manifest response."}

    log.info(f"Extracted {len(record_locators)} RecordLocators in _fetch_record_locators")
    return {"success": True, "data": record_locators}

async def _get_booking_by_rl(token: str, record_locator: str):
    base_url = NAVITAIRE_BASE_URL.rstrip('/')
    endpoint = NAVITAIRE_BOOKING_BY_RL_ENDPOINT
    if not endpoint:
        log.warning("NAVITAIRE_BOOKING_BY_RL_ENDPOINT is not set in .env, using a default value.")
        endpoint = "api/nsk/v1/bookings"

    url = f"{base_url}/{endpoint.strip('/')}/{record_locator}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if NAV_USER_KEY:
        headers["user_key"] = NAV_USER_KEY

    log.debug(f"Requesting booking details")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code in (401, 403):
            token = await get_navitaire_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.get(url, headers=headers)

        if resp.status_code >= 400:
            log.error(f"Booking API returned status {resp.status_code} for RL {record_locator}. Response: {resp.text}")
            return {"success": False, "record_locator": record_locator, "error": "api_error", "status_code": resp.status_code, "message": "Failed to get booking details"}
        
        return {"success": True, "record_locator": record_locator, "data": resp.json()}

# ----------- Navitaire Flight Search Tool ------------------
from tools.navitaire_flight_search import NavitaireFlightSearchFetcher
flight_search_fetcher = NavitaireFlightSearchFetcher(get_navitaire_token)

@mcp.tool("navitaire_flight_search")
async def navitaire_flight_search(
    origin: str,
    destination: str,
    begin_date: str = None,
    end_date: str = None,
    passenger_count: int = 1,
    promotion_code: str = "",
    max_connections: int = 0,
    product_classes: list = None,
    sort_options: list = None
) -> Dict[str, Any]:
    """
    Search for flights using Navitaire API.
    
    Sample Input:
        {"origin": "DEL", "destination": "BOM", "passenger_count": 1}
    
    Sample Output:
        {"success": true, "data": {flight search results}, "search_criteria": {original search parameters}}
    
    Parameters:
        - origin: 3-letter departure airport code (e.g., "DEL")
        - destination: 3-letter arrival airport code (e.g., "BOM")
        - begin_date: Optional departure date in YYYY/MM/DD format (defaults to today if not provided)
        - end_date: Optional return date in YYYY/MM/DD format
        - passenger_count: Number of adult passengers (default: 1)
        - promotion_code: Optional promotional code for discounts
        - max_connections: Maximum number of connections allowed (default: 0 for direct flights)
        - product_classes: List of fare classes to search (default: ["N", "R", "J", "O"])
        - sort_options: List of sorting preferences (default: ["EarliestDeparture"])
    
    Output Fields:
        - success: Boolean indicating if the search was successful
        - data: Flight search results from Navitaire API
        - search_criteria: Echo of the search parameters used
        - error: Error message if search failed
    
    Error Handling:
        - Returns error details if mandatory parameters are missing
        - Network or API connectivity errors are logged and returned
        - Invalid airport codes or dates result in validation errors
    
    Usage:
        - Provide origin, destination, and begin_date as mandatory parameters
        - Use IATA 3-letter airport codes (DEL, BOM, CCU, etc.)
        - Date format should be YYYY/MM/DD
        - Results include available flights with pricing and schedule information
    """
    
    return await flight_search_fetcher.search_flights(
        origin=origin,
        destination=destination,
        begin_date=begin_date,
        end_date=end_date,
        passenger_count=passenger_count,
        promotion_code=promotion_code,
        max_connections=max_connections,
        product_classes=product_classes,
        sort_options=sort_options
    )

# ----------- NPS APIS ------------------
from tools.salesforce_query import (
    SalesforceQueryFetcher,
    get_salesforce_token,
    _salesforce_job_query,
    _salesforce_job_status,
    _salesforce_fetch_results,
    calculate_nps_score
)

salesforce_query_fetcher = SalesforceQueryFetcher(
    get_salesforce_token,
    _salesforce_job_query,
    _salesforce_job_status,
    _salesforce_fetch_results,
    calculate_nps_score
)

@mcp.tool("salesforce_query_tool")
def salesforce_query_tool(igacode: str = None):
    """
    Fetch NPS (Net Promoter Score) data for crew members from Salesforce.

    Sample Input (single line):
        {"igacode": "IGA46671"}
        or {} # for bulk data

    Sample Output (single line):
        {"success":true,"igacode":"IGA46671","nps_score":-73.2}

    Parameters:
        - igacode: Optional crew IGA code (e.g., "IGA46671"). If provided, returns NPS data only for that crew member. If not provided or empty input, returns bulk NPS data for all crew members.

    Output Field Summary:
        - status: Operation status ("success" or "error")
        - data: Array of NPS records
            - nps_score: Numeric NPS score (-100 to +100 scale)
            - iga: Crew IGA identifier

    Error Handling:
        - Invalid IGA codes return empty data 


    """
    return salesforce_query_fetcher.salesforce_query(igacode)


#-----------------Flight_Schedular-----------------------#

log = logging.getLogger(__name__)

# --- JOC TOKEN CACHE & ACCESSOR ---
# If you already have this block, keep your existing one.
_JOC_TOKEN_CACHE = {"value": None, "expires_at": 0.0}
log.debug("Initialized JOC token cache")

def _get_joc_access_token():
    now = time.time()
    # Reuse cached token if not near expiry (keep 60s buffer)
    if _JOC_TOKEN_CACHE["value"] and _JOC_TOKEN_CACHE["expires_at"] > now + 60:
        log.debug("Using cached JOC token")
        return _JOC_TOKEN_CACHE["value"]

    try:
        url = JOC_TOKEN_ENDPOINT
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        payload = {
            "client_id": JOC_CLIENT_ID,
            "client_secret": JOC_CLIENT_SECRET,
            "scope": JOC_SCOPE,
            "grant_type": "client_credentials"
        }

        with httpx.Client(timeout=20) as client:
            response = client.post(url, headers=headers, data=payload)
            if response.status_code >= 400:
                log.error(f"JOC token request failed with status {response.status_code}: {response.text}")
                response.raise_for_status()

            data = response.json()
            access_token = data.get('access_token')
            if not access_token:
                raise RuntimeError("JOC access_token not found in response")

            # Cache for ~59 minutes
            _JOC_TOKEN_CACHE["value"] = access_token
            _JOC_TOKEN_CACHE["expires_at"] = now + 3540
            log.debug("JOC token successfully retrieved")
            return access_token

    except Exception as e:
        log.exception('Failed to get JOC access token')
        raise

# --- Flight schedule fetcher (NEW IMPORT) ---
from tools.flight_schedule_fetcher import FlightScheduleFetcher
flight_schedule_fetcher = FlightScheduleFetcher(_get_joc_access_token)

# --- MCP TOOL: flight_schedule_data ---


@mcp.tool("flight_schedule_data")
def flight_schedule_data(input: str) -> dict:
    """
flight_schedule_data Tool Description:

Fetches schedule information for a specific flight using JOC API based on flight number, date, and departure station. 

Input Format
{"input": "2386 2025-11-11 DEL"}

Input Components (Auto-Parsed)
- Flight Number: 2-5 digit numeric (automatically prefixed with '6E')
- Date: YYYY-MM-DD , Input date is in UTC format.
- Start Station: 3-letter IATA airport code (for eg. "DEL")

Output Format
{
  "id": "6E#2025-11-11#6350#DEL#LKO",
  "flightNumber": 6350,
  "startTimeOffset": "05:30",
  "endTimeOffset": "05:30",
  "startStation": "DEL",
  "endStation": "LKO",
  "flightStatus": "",
  "scheduledStartTime": "2025-11-11T00:00:00",
  "scheduledEndTime": "2025-11-11T01:05:00",
  "operation": {
    "estimatedTimes": {
      "offBlock": null,
      "inBlock": "2025-11-11T00:57:00Z",
      "takeoffTime": "2025-11-11T00:02:00Z",
      "landingTime": "2025-11-11T00:52:00Z"
    },
    "actualTimes": {
      "offBlock": "2025-11-10T23:52:00Z",
      "inBlock": "2025-11-11T00:54:00Z",
      "takeoffTime": "2025-11-11T00:07:00Z",
      "landingTime": "2025-11-11T00:52:00Z",
      "doorClose": "2025-11-10T23:44:00Z"
    }
  },
  "handling": {
    "serviceType": "J"
  },
  "equipment": {
    "aircraft": {
      "registration": "VTILU",
      "type": "323"
    },
    "plannedaircraftTypeICAO": "A321"
  }

Key Data Fields

Flight Identification
- id: Unique flight identifier (6E#YYYY-MM-DD#NNNN#DEP#ARR)
- flightNumber: Numeric flight number (without 6E prefix)
- startStation/endStation: IATA departure/arrival codes
- flightStatus: Current status (Scheduled, Delayed, Cancelled, etc.)

Timing Information
- scheduledStartTime/scheduledEndTime: Planned departure/arrival (Indian Standard Time IST)
- startTimeOffset/endTimeOffset: Timezone offsets for local times(Indian Standard Time IST)
- operation.estimatedTimes: Current estimated departure/arrival
- operation.actualTimes: Actual times (if flight completed)

Equipment Details
- equipment.aircraft.registration: Aircraft tail number
- equipment.aircraft.type: Aircraft type code
- equipment.plannedaircraftTypeICAO: ICAO aircraft designation


Flight Not Found Response
{
  "success": false,
  "match_count": 0,
  "data": null,
  "error": "Flight 6E2386 not found for date 2025-11-11 from DEL"
}

"""
    try:
        if not input or not isinstance(input, str):
            return {
                "success": False,
                "error": "Input must be a single string containing: <numeric flight number> <date> <startStation>.",
                "data": None
            }

        import re

        text = input.strip()

        # Numeric flight number: allow 2-5 digits for Indigo flights
        fn_match = re.search(r"\b(\d{2,5})\b", text)
        # Date: YYYY-MM-DD or YYYY/MM/DD
        date_match = re.search(r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b", text)
        # Start station: 3-letter IATA (DEL, BOM, BLR, etc.)
        # Try to find an explicit "from XXX" first; else grab any 3-letter token and use the last one.
        station_match = re.search(r"\bfrom\s+([A-Za-z]{3})\b", text, flags=re.IGNORECASE)
        start_station = None
        if station_match:
            start_station = station_match.group(1).upper()
        else:
            # Fallback: pick the last 3-letter token as station
            all_stations = re.findall(r"\b([A-Za-z]{3})\b", text)
            if all_stations:
                start_station = all_stations[-1].upper()

        if not fn_match or not date_match or not start_station:
            return {
                "success": False,
                "error": "Could not parse flight number, date, and startStation. Example: '2993 2025-11-11 DEL'.",
                "data": None
            }

        numeric_flight = fn_match.group(1)       # e.g., "2386"
        date_str = date_match.group(1)           # e.g., "2025-11-11" or "2025/11/11"
        # Validate start station
        if not re.fullmatch(r"[A-Za-z]{3}", start_station):
            return {"success": False, "error": f"Invalid startStation: {start_station}. Expected IATA code like 'DEL'.", "data": None}

        result = flight_schedule_fetcher.get_flight_by_number_and_date(numeric_flight, date_str, start_station)
        return result

    except Exception as e:
        log.exception("flight_schedule_data tool failed")
        return {"success": False, "error": f"Unexpected error in flight_schedule_data: {str(e)}", "data": None}

# ----------------Breathalyzer Tool MCP -----------------
BREATH_API_BASE_URL = os.getenv("BREATH_API_BASE_URL")
BREATH_USER_KEY     = os.getenv("BREATH_USER_KEY")
BREATH_X_API_KEY    = os.getenv("BREATH_X_API_KEY")
BREATH_TIMEOUT_MS   = int(os.getenv("BREATH_TIMEOUT_MS", "15000"))
 
async def _breath_post_async(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Performs the HTTP POST to DigiBreathalyzer.
    Returns: { status_code: int, json: Any } or { status_code, text }.
    """
    if not BREATH_API_BASE_URL or not BREATH_USER_KEY or not BREATH_X_API_KEY:
        raise RuntimeError("BREATH_API_BASE_URL/BREATH_USER_KEY/BREATH_X_API_KEY must be set")
    headers = {
        "content-type": "application/json",
        "user-key": BREATH_USER_KEY,   # hyphen per upstream
        "x-api-key": BREATH_X_API_KEY,
    }
    async with httpx.AsyncClient(timeout=BREATH_TIMEOUT_MS / 1000.0) as client:
        resp = await client.post(BREATH_API_BASE_URL, headers=headers, json=payload)
        status = resp.status_code
        try:
            return {"status_code": status, "json": resp.json()}
        except json.JSONDecodeError:
            return {"status_code": status, "text": resp.text}
 
from tools.breathalyzer import BreathalyzerFetcher   
breath_fetcher = BreathalyzerFetcher(_breath_post_async)
@mcp.tool("check_breathalyzer")
async def check_breathalyzer(
    dutytype: str,
    flightStartDate: str,
    flightNo: int | None = None,
    dutyCode: str | None = None,
) -> Dict[str, Any]:
    """
   Check breathalyzer test requirements and status for crew members based on duty type and flight details.
   Sample Input (single line):
       {"dutytype": "Flight", "flightStartDate": "2025-12-24T06:00:00", "flightNo": 6123, "dutyCode": "FLT"} # str
   Sample Output (single line):
        Input date is in UTC format.
       {"status": "success", "data": {"testRequired": true, "testStatus": "pending", "dutyType": "Flight", "flightNumber": 6123, "testDeadline": "2025-12-24T05:30:00"}}
   Output Field Summary:
       - status: Request status ("success" or "error")
       - data: Breathalyzer test information
           - testRequired: Whether breathalyzer test is required (boolean)
           - testStatus: Current status of the test (string)
           - dutyType: Type of duty (Flight, Ground, etc.)
           - flightNumber: Associated flight number (if applicable)
           - testDeadline: Deadline for completing the test (ISO format)
           - additionalInfo: Any additional test requirements or notes
   Error Handling:
       - Returns error details if mandatory parameters are missing or invalid.
       - Network or API connectivity errors are logged and returned.
       - Invalid duty types or flight dates result in validation errors.
   Usage Guidance:
       - Provide dutytype and flightStartDate as mandatory parameters.
       - flightNo and dutyCode are optional but recommended for flight duties.
       - Use ISO format for flightStartDate (YYYY-MM-DDTHH:mm:ss).
       - Results help ensure compliance with breathalyzer testing requirements.
   """
 
    return await breath_fetcher.check_breathalyzer(dutytype, flightStartDate, flightNo, dutyCode)


# ----------------Snowflake Custom Query Tool MCP -----------------
from tools.snowflake_survey_salesforce import (
    SnowflakeConnection, 
    SnowflakeCustomQueryFetcher
)

# Initialize Snowflake connection
sf_conn = SnowflakeConnection()

# Initialize Snowflake query fetcher
snowflake_query_fetcher = SnowflakeCustomQueryFetcher(lambda: sf_conn)

@mcp.tool("snowflake_survey_salesforce")
def execute_custom_snowflake_query(query: str, limit: int = 100) -> str:
    """Tool Description

Overview:
Executes SQL queries against NPS (Net Promoter Score) datasets for customer satisfaction analysis. Provides access to passenger feedback, experience ratings.

CRITICAL: Use this tool for ALL NPS-related queries including:
- NPS scores, ratings, and satisfaction analysis
- Customer feedback and passenger experience data  
- Promoters, Passives, Detractors analysis
- Route satisfaction and crew performance correlation
- Passenger survey responses and experience touchpoints

Keywords that indicate this tool should be used:
- "NPS", "Net Promoter Score", "satisfaction", "promoter", "detractor", "passive", "customer experience"
- "survey", "rating", "passenger feedback", "satisfaction score", "customer satisfaction"


Input/Output Specification:

Input Format:
{"query": "SELECT * FROM V_SALESFORCE_SURVEY WHERE PASSENGER_BOOKINGR_DEPARTURE_DATE__C > '2025-01-01'"}

Query Requirements:
- Allowed: Only SELECT statements


Output Format:
{
  "result": "[{\"NPS_TYPE__C\":\"Promoter\",\"PASSENGER_BOOKINGR_FLTNBR__C\":\"2386\",\"PASSENGER_BOOKINGR_DEP__C\":\"DEL\",\"PASSENGER_BOOKINGR_ARR__C\":\"BOM\",\"BOOKING_EXPERIENCE__C\":5,\"CHECK_IN_EXPERIENCE__C\":4,\"ON_BOARD_EXPERIENCE__C\":4,\"PLEASE_SHARE_YOUR_REASONS_FOR_THE_RATING__C\":\"Excellent service and on-time performance\"}]"
}


Core NPS Metrics:

- NPS_TYPE__C: Classification ("PROMOTORS__C", "PASSIVE__C", "DETRACTORS__C")
  - PROMOTORS__C: Highly satisfied customers who are praising the services
    - **ALWAYS use when analyzing customer appreciation, praise, or positive feedback**
  - PASSIVE__C: Satisfied but not enthusiastic customers who are neutral about services
    - **Use for neutral or mixed feedback scenarios**
  - DETRACTORS__C: Unsatisfied customers who might discourage others from choosing IndiGo
    - **ALWAYS use when analyzing customer complaints, negative feedback, or service issues**
- RESPONSE_STATUS__C: Survey completion status ( Started/Completed)
- SURVEY_NAME__C: Survey campaign identifier (Travel Experience)


Example NPS_TYPE__C Queries:
-- Count promoters (NOT "Promotor")
SELECT COUNT(*) FROM V_SALESFORCE_SURVEY WHERE NPS_TYPE__C = 'PROMOTORS__C'

IMPORTANT: NPS_TYPE__C Selection Guidelines:
When interpreting user queries about NPS categories, use these specific value matches:
- "promoter/promoters/promotor" → Use 'PROMOTORS__C'
- "passive/passives" → Use 'PASSIVE__C'  
- "detractor/detractors" → Use 'DETRACTORS__C'

CRITICAL: Route-Based Queries:
When querying for specific routes or city pairs, ALWAYS use PASSENGER_BOOKINGR_DEP__C and PASSENGER_BOOKINGR_ARR__C fields separately instead of PASSENGER_BOOKINGR_CITY_PAIR__C

CORRECT Route Query Format:
-- For DEL-BOM route queries, use PASSENGER_BOOKINGR_DEP__C and PASSENGER_BOOKINGR_ARR__C separately
SELECT COUNT(*) FROM V_SALESFORCE_SURVEY WHERE PASSENGER_BOOKINGR_DEP__C = 'DEL' AND PASSENGER_BOOKINGR_ARR__C = 'BOM'

-- NOT: WHERE PASSENGER_BOOKINGR_CITY_PAIR__C = 'DEL-BOM' (avoid this for connecting flights)

Route Query Guidelines:
- "DEL to BOM" / "DEL-BOM" → Use PASSENGER_BOOKINGR_DEP__C = 'DEL' AND PASSENGER_BOOKINGR_ARR__C = 'BOM'
- "Mumbai to Delhi" → Use PASSENGER_BOOKINGR_DEP__C = 'BOM' AND PASSENGER_BOOKINGR_ARR__C = 'DEL'  
- "flights from DEL" → Use PASSENGER_BOOKINGR_DEP__C = 'DEL'
- "flights to BOM" → Use PASSENGER_BOOKINGR_ARR__C = 'BOM'
- "domestic routes" → Use appropriate Indian airport codes
- "international routes" → Use international airport codes

Why use PASSENGER_BOOKINGR_DEP__C/PASSENGER_BOOKINGR_ARR__C instead of PASSENGER_BOOKINGR_CITY_PAIR__C:
- Handles connecting flights with multiple cities correctly
- More flexible for partial route queries (departure-only or arrival-only)


Flight Information:
- PASSENGER_BOOKINGR_FLTNBR__C: IndiGo flight number (1454) (No 6E prefix)
- PASSENGER_BOOKINGR_DEP__C: Departure airport codes(for eg. BOM)
- PASSENGER_BOOKINGR_ARR__C: Arrival airport codes(for eg. DEL)
- PASSENGER_BOOKINGR_CITY_PAIR__C: Route designation (DEP-ARR format)
- PASSENGER_BOOKINGR_DEPARTURE_DATE__C/PASSENGER_BOOKINGR_ARRIVAL_DATE__C : Flight scheduling information (for eg. 2025-12-14). 

**When date filtering is requested, ALWAYS use PASSENGER_BOOKINGR_ARRIVAL_DATE__C column as the primary date field:**
**IMPORTANT: Do not use timezone when filtering with PASSENGER_BOOKINGR_ARRIVAL_DATE__C - use plain date format (YYYY-MM-DD)**
**Examples:**
- "Show NPS data for December 14, 2025" → WHERE PASSENGER_BOOKINGR_ARRIVAL_DATE__C = '2025-12-14'
- "Flights between Jan 1-15, 2025" → WHERE PASSENGER_BOOKINGR_ARRIVAL_DATE__C BETWEEN '2025-01-01' AND '2025-01-15'


- PASSENGER_BOOKINGR_DEPARTURE_DATE_TIME__C: Flight scheduling information ( for eg. 2025-12-17 06:18:00 UTC)
- PASSENGER_BOOKINGR_EQUIPMENT_TYPE__C: Aircraft type(320,321,ATR,789,738,77W)
- START_DATE_TIME__C: when the survey started not flight start time. (for eg. 2025-12-17 06:18:00 UTC)

Passenger Details:
- PASSENGER_BOOKINGR_ID: Passenger Name Record
- PASSENGER_BOOKINGR_PAX_TYPE__C: Passenger category (ADT/CHD)

Experience Ratings (0-5 Scale):
- BOOKING_EXPERIENCE__C: Online/offline booking process satisfaction
- PRE_TRAVEL_INFORMATION_EXPERIENCE__C: Pre-flight information quality
- CHECK_IN_EXPERIENCE__C: Airport check-in process satisfaction
- BOARDING_EXPERIENCE__C: Gate and boarding process satisfaction
- ON_BOARD_EXPERIENCE__C: In-flight service satisfaction
- ARRIVAL_EXPERIENCE__C: Baggage and arrival process satisfaction

Detailed Service Quality:
Booking Process:
- EASE_OF_BOOKING_ITINERARY_MEALS_S__C: Booking system usability (scale 0-5)
- REQUIRED_INFORMATION_AVAILABLE_ON_WEBSIT__C: Website information adequacy (scale 0-5)
- PASSENGER_BOOKINGR_SOURCE_OF_BOOKING__C: Booking channel (Travel Agent, IndiGo Website, IndiGo App,  Others-1APA, Airport, Others-STFZ, IndiGo Call Center, Others-APTO, Others-BREC, Others-MAPP, Others-WWSK)

Check-in Process:
- PASSENGER_BOOKINGR_CHECKIN_TYPE__C: Method used (WEBCheckIn, Counter, KIOSK)
- CHECK_IN_PROCESS_WAS_EASY_ONLINE_KIOSK__C: Process ease rating (scale 0-5)
- TIME_TAKEN_TO_CHECK_IN_COUNTER_WITHIN15__C: Time efficiency rating (scale 0-5)
- STAFF_EFFICIENCY_AT_THE_COUNTER__C: Counter staff performance (scale 0-5)

Onboard Experience:
- CREW_HELPFULNESS__C: Cabin crew service quality (scale 0-5)
- CABIN_CLEANLINESS__C/TOILET_CLEANLINESS__C: Aircraft hygiene ratings (scale 0-5)
- CLARITY_OF_CREW_ANNOUNCEMENTS__C: Communication effectiveness (scale 0-5)
- SNACKS_AND_BEVERAGE_IF_EXPERIENCED__C: Food service quality (scale 0-5)
- QUALITY_OF_PRE_BOOKED_SNACKS__C: Pre-ordered meal satisfaction (scale 0-5)

Operational Context:
- PASSENGER_BOOKINGR_DEPARTURE_DELAY__C: Flight delay indicator (Y/N)
- PASSENGER_BOOKINGR_DELAYINFLIGHT__C: Delay duration ( 30 min to 2 hours, Less than 30 min , Before time, Less than 15 min, 2 hours to 3 hours, More than 3 hours, On time)
- PASSENGER_BOOKINGR_FASTFORWARDSERVICE__C: Premium service usage (TRUE/FALSE)
- PASSENGER_BOOKINGR_PREBOOKEDMEAL__C: Pre-booked meal indicator (TRUE/FALSE)
- PASSENGER_BOOKINGR_ONBOARD_MEAL_PURCHASED__C: Meal service indicators (Yes/No)

Open-Ended Feedback:
- PLEASE_SHARE_YOUR_REASONS_FOR_THE_RATING__C: Detailed passenger comments and feedback
"""
    return snowflake_query_fetcher.execute_custom_query(query, limit)

# --- imports ---
from typing import Optional, Dict, Any  # if not already present
from tools.clms_sql_query import ClmsService

# --- instantiate (put this near other singletons) ---
clms_service = ClmsService()

# --- register the single MCP tool ---
@mcp.tool("clms")
async def clms(action: str, payload: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None):
    """
    Single CLMS tool.

    Use `action` to select an operation and pass parameters in `payload`.

    Supported actions:
      - sql_query
        payload: { "query": "...", "limit": 1000, "as_csv": false }

      - validate_leave_request
        payload: { "crew_id": int, "leave_type_id": int, "from_dt": "YYYY-MM-DD", "to_dt": "YYYY-MM-DD", "leave_year_id": int? }

      - create_leave_request
        payload: { "crew_id": int, "leave_type_id": int, "from_dt": "...", "to_dt": "...", "comments": str?, "leave_year_id": int?, "actor_id": str? }

      - approve_leave_request
        payload: { "leave_detail_id": int, "decision": "approve"|"reject", "approver_id": str?, "remarks": str? }

      - adjust_leave_balance
        payload: { "crew_id": int, "leave_type_id": int, "leave_year_id": int, "delta": float, "reason": str?, "actor_id": str? }

    Returns a uniform envelope:
      { "action": "...", "success": true|false, "data": {...} | null, "warnings": [], "error": {code, message}? }
    """
    return clms_service.handle(action, payload, idempotency_key)

@mcp.tool("crewportal_sqlserver")
async def sqlserver_run_query(query: str):
    """
    Description: Execute SQL queries against SQL Server database and return results as JSON.
    
    Sample Input (single line):
        {"query": "SELECT TOP 10 * FROM TableName WHERE DateColumn > '2025-01-01'"}
    
    Output Field Summary:
    - Returns query results as JSON array of objects
    - Each row is represented as a dictionary with column names as keys
    - Supports all standard SQL Server data types
    - Datetime objects are serialized to ISO format strings
    
    Error Handling:
    - Returns error details if query is missing, empty, or contains unsafe operations.
    - Only SELECT queries are allowed; other operations are blocked for security.
    - Database connection, authentication, or permission errors are returned with user-friendly messages.
    
    Usage Guidance:
    - Provide a valid SELECT SQL query as input.
    - Use output fields for data analysis, reporting, or automation.
    
    """
    return await _execute_sqlserver_query(query)

from tools.aims_crew_category import CrewCategoryFetcher
crew_category_fetcher = CrewCategoryFetcher(AIMS_BASE, _aims_fetch_token, _aims_auth_headers)

@mcp.tool("aims_crew_category")
async def aims_crew_category(id: str, active: int = 1, catid: str = None):
    """
    Fetch crew category information from AIMS API.
    
    This tool retrieves crew category data based on crew ID and other optional parameters.
    
    Parameters:
    - id (required): Crew ID (e.g., "2281")
    - active (optional): Active status filter (1 = active, 0 = inactive). Default is 1
    - catid (optional): Category ID to filter specific categories
    
    Sample Input (single line):
        {"id": "2281", "active": 1, "catid": "70"}
    
    Sample Output:
        {"success": true, "data": {...crew category data...}}
    
    Returns crew category information including qualifications, certifications, and other category details.
    """
    return await crew_category_fetcher.get_crew_category(id, active, catid)

@mcp.tool("missing_flight_report_db")
async def missing_flight_report_db(startDateTime: str = None, endDateTime: str = None, startStation: str = None):
    """
Find missing flight reports by comparing JOC flight data with database flight reports.

This tool fetches flight data from JOC API and compares it with flight reports 
in the database to identify flights that don't have corresponding flight reports.

Parameters:
    - startDateTime : Start date and time in format "YYYY-MM-DDTHH:MM" 
                      (defaults to today's date)
    - endDateTime   : End date and time in format "YYYY-MM-DDTHH:MM" 
                      (defaults to today's date end)
    - startStation  : (optional) station code (e.g., "DEL", "BOM")

Returns:
    - List of flights from JOC that don't have corresponding flight reports 
      in the database.
    - Each missing flight includes: 
          flightNumber, 
          flightDate, 
          departure, 
          arrival, 
          scheduledTime.

Example Input:
    Input date is in UTC format.
    {"startDateTime": "2026-02-10T00:00", 
     "endDateTime": "2026-02-10T23:59", 
     "startStation": "DEL"}

The summary returned by this tool now includes a new field 
`missing_by_base`, which provides a base-wise breakdown of:
    - total_flights  
    - filled_reports  
    - missing_reports  

This field is now a LIST of dictionaries (one per station/base processed).

Expected structure:

"summary": {
    "query_date": "YYYY-MM-DD",
    "total_stations_processed": INTEGER,
    "total_db_reports": INTEGER,
    "total_missing_reports": INTEGER,
    "missing_by_base": [
        {
            "base": "<IATA Code>",
            "total_flights": INTEGER,
            "filled_reports": INTEGER,
            "missing_reports": INTEGER
        },
        {
            "base": "<IATA Code>",
            "total_flights": INTEGER,
            "filled_reports": INTEGER,
            "missing_reports": INTEGER
        }
        // ... repeated for every processed station
    ]
}

Purpose:
- Provides a clear per-base reporting completeness overview.
- Helps identify stations with high missing-flight-report counts.
- Ensures consistent summary output for both single-station 
  and multi-station queries.
    """
    
    try:
        # Set default dates if not provided
        if not startDateTime or not endDateTime:
            today = datetime.now()
            if not startDateTime:
                startDateTime = today.strftime("%Y-%m-%dT00:00")
            if not endDateTime:
                endDateTime = today.strftime("%Y-%m-%dT23:59")
        
        # List of Indian stations
        indian_stations = ["DEL", "AMD", "BOM", "CCU", "MAA", "HYD", "LKO", "PNQ", "COK", "IXC", "IDR", "JAI", "BLR"]
        
        # If startStation provided, process only that station
        if startStation:
            stations_to_process = [startStation]
        else:
            stations_to_process = indian_stations
        
        # Get flight date for database query
        flight_date = startDateTime.split("T")[0]
        
        # Get database flight reports for the date
        db_query = f"""
        SELECT ReportId,
               FlightDate,
               FlightNo,
               DEP,
               ARR
        FROM T_FlightIssueGeneralInfo_Prod
        WHERE FlightDate = '{flight_date}'
        ORDER BY ReportId DESC;
        """
        
        log.info(f"Querying database for flight reports on {flight_date}")
        db_result = await _execute_sqlserver_query(db_query)
        
        if db_result.get("error"):
            return {"error": f"Database query failed: {db_result['error']}"}
        
        # Parse database results
        db_flight_numbers = set()
        if db_result.get("result"):
            db_data = json.loads(db_result["result"])
            for report in db_data:
                if report.get("FlightNo"):
                    db_flight_numbers.add(str(report["FlightNo"]))

        # Get JOC access token
        joc_token = _get_joc_access_token()
        if isinstance(joc_token, dict) and joc_token.get("error"):
            return {"error": f"Failed to get JOC token: {joc_token['error']}"}
        
        # JOC API configuration
        joc_url = JOC_FLIGHT_TIME_ENDPOINT
        if not joc_url:
            return {"error": "JOC_FLIGHT_TIME_ENDPOINT not configured"}
        
        headers = {
            "Authorization": f"Bearer {joc_token}",
            "Content-Type": "application/json",
            "User_key": JOC_USER_KEY
        }
        
        # Process each station
        result = {}
        total_missing_count = 0
        
        for station in stations_to_process:           
            # Prepare JOC request payload
            payload = {
                "startDateTime": startDateTime,
                "endDateTime": endDateTime,
                "startStation": station,
                "page": 1,
                "size": 3000
            }
            
            try:
                # Fetch flight data from JOC
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(joc_url, json=payload, headers=headers)
                    joc_data = response.json()
                
                if not joc_data.get("dataAvailable") or not joc_data.get("data"):
                    result[station] = {
                        "total_joc_flights": 0,
                        "missing_flights_count": 0,
                        "missing_flights": []
                    }
                    continue
                
                   # Extract required fields from JOC data
                joc_flights = []
                joc_flight_numbers = []
                
                for flight in joc_data["data"]:
                    if flight.get("flightNumber"):
                        flight_info = {
                            "id": flight.get("id", ""),
                            "flightNumber": str(flight["flightNumber"]),
                            "startStation": flight.get("startStation", station),
                            "endStation": flight.get("endStation", ""),
                            "scheduledStartTime": flight.get("scheduledStartTime", ""),
                            "scheduledEndTime": flight.get("scheduledEndTime", ""),
                            "actualStartTime": (flight.get("actualTimes") or {}).get("takeoffTime"),
                            "actualEndTime":   (flight.get("actualTimes") or {}).get("landingTime"),
                            "plannedaircraftTypeICAO":flight.get("plannedaircraftTypeICAO", ""),

                        }
                        joc_flights.append(flight_info)
                        joc_flight_numbers.append(str(flight["flightNumber"]))
                print("joc_flight_numbers:", joc_flight_numbers,"joc_flights:", joc_flights)
                        
                # Find missing flights (in JOC but not in database)
                missing_flights = []
                for joc_flight in joc_flights:
                    if joc_flight["flightNumber"] not in db_flight_numbers:
                        missing_flights.append(joc_flight)
                
                station_missing_count = len(missing_flights)
                total_missing_count += station_missing_count
                
                result[station] = {
                    "total_joc_flights": len(joc_flights),
                    "missing_flights_count": station_missing_count,
                    "missing_flights": missing_flights
                }
                
            except httpx.HTTPError as e:
                log.error(f"HTTP error fetching JOC data for {station}: {e}")
                result[station] = {"error": f"Failed to fetch JOC data: {str(e)}"}
            except json.JSONDecodeError as e:
                log.error(f"JSON decode error for {station}: {e}")
                result[station] = {"error": f"Failed to parse JOC response: {str(e)}"}
            except Exception as e:
                log.error(f"Unexpected error for {station}: {e}")
                result[station] = {"error": f"Unexpected error: {str(e)}"}
        
# Prepare final response - always use stations format
        # Derive station-wise rollups for the summary list
        missing_by_base = []
        total_missing_reports = 0  # we will recompute from per-base rollups to stay consistent
        for st, data in result.items():
            if not isinstance(data, dict):
                continue
            total_flights = int(data.get("total_joc_flights", 0))
            missing_reports = int(data.get("missing_flights_count", 0))
            filled_reports = max(total_flights - missing_reports, 0)
            total_missing_reports += missing_reports
            missing_by_base.append(
                {
                    "base": st,
                    "total_flights": total_flights,
                    "filled_reports": filled_reports,
                    "missing_reports": missing_reports,
                }
            )

        # Build summary based on whether single station or multi-station query
        summary = {
            "query_date": flight_date,
            "total_stations_processed": len(stations_to_process),
            "total_db_reports": len(db_flight_numbers),
            "total_missing_reports": total_missing_reports,
            "missing_by_base": missing_by_base,
        }

        return {
            "summary": summary,
            "stations": result,
        }

    except Exception as e:
        log.error(f"Unexpected error in missing_flight_report_db: {e}")
        return {"error": f"Unexpected error: {str(e)}"}

#-------------------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("Starting FastMCP server on port 8080")
    mcp.run(transport="http", port=8080, host="0.0.0.0")
