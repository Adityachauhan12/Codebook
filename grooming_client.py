import httpx
import os
from typing import Dict, Any, Optional
from logger_config import get_logger

class GroomingClient:
    def __init__(self):
        self.base_url = os.getenv("GROOMING_SERVICE_URL", "https://6e-ifs-grooming-be-dev.goindigo.in")
        self.timeout = float(os.getenv("GROOMING_TIMEOUT", "30.0"))
        self.logger = get_logger("grooming_client")
    
    async def get_insights(self, days: Optional[int] = None, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """Call /v1/insights with optional parameters"""
        try:
            params = {"page": page, "pageSize": page_size}
            if days:
                params["days"] = days
                
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                self.logger.info(f"Calling grooming service: {self.base_url}/v1/insights")
                response = await client.get(f"{self.base_url}/v1/insights", params=params)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            self.logger.error("Grooming service timeout")
            raise Exception("Grooming service is not responding")
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Grooming service HTTP error: {e.response.status_code}")
            raise Exception(f"Grooming service error: {e.response.status_code}")
        except Exception as e:
            self.logger.error(f"Grooming service connection error: {e}")
            raise Exception("Unable to connect to grooming service")
    
    async def get_insights_by_day(self, days: int) -> Dict[str, Any]:
        """Call /v1/insights?days={days}"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                response = await client.get(f"{self.base_url}/v1/insights", params={"days": days})
                response.raise_for_status()
                return response.json()
        except Exception as e:
            self.logger.error(f"Error getting insights by day: {e}")
            raise
    
    async def get_insights_by_iga(self, iga_code: str, crew_name: Optional[str] = None, date_from: str = "", date_to: str = "") -> Dict[str, Any]:
        """Call /v1/individual-analysis with correct parameter names"""
        params = {"igaCode": iga_code, "dateFrom": date_from, "dateTo": date_to}
        if crew_name:
            params["crewName"] = crew_name
            
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                response = await client.get(f"{self.base_url}/v1/individual-analysis", params=params)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            self.logger.error(f"Error getting insights by IGA: {e}")
            raise
    
    async def check_grooming_video(self, file_data: bytes, filename: str, name: str, iga_code: str, base: Optional[str] = None, terminal: Optional[str] = None) -> Dict[str, Any]:
        """Call /check-grooming-video with correct form parameters"""
        try:
            files = {"video": (filename, file_data, "video/mp4")}
            data = {
                "name": name,
                "iga_code": iga_code
            }
            if base:
                data["base"] = base
            if terminal:
                data["terminal"] = terminal
                
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                response = await client.post(f"{self.base_url}/check-grooming-video", files=files, data=data)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            self.logger.error(f"Error checking grooming video: {e}")
            raise
    
    async def check_grooming_photo(self, image_base64: str, crew_name: Optional[str] = None, iga_code: Optional[str] = None, base: Optional[str] = None, terminal: Optional[str] = None, department: Optional[str] = None) -> Dict[str, Any]:
        """Call /check-grooming with JSON payload"""
        try:
            payload = {"imageBase64": image_base64}
            if crew_name:
                payload["crewName"] = crew_name
            if iga_code:
                payload["igaCode"] = iga_code
            if base:
                payload["base"] = base
            if terminal:
                payload["terminal"] = terminal
            if department:
                payload["department"] = department
                
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                response = await client.post(f"{self.base_url}/check-grooming", json=payload)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            self.logger.error(f"Error checking grooming photo: {e}")
            raise