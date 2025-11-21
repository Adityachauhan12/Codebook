from datetime import datetime
from typing import Dict, Any, List
import uuid
from google.cloud import bigquery
import os
from logger_config import get_logger
from tools import mcp_tool_call

logger = get_logger("urti_leaves")

client = bigquery.Client(project=os.getenv("GCP_PROJECT_ID"))

def table_ref() -> str:
    ref = f"{os.getenv('GCP_PROJECT_ID')}.{os.getenv('BQ_DATASET')}.{os.getenv('BQ_TABLE')}"
    return ref

def query_rows(sql: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    try:
        job_config = bigquery.QueryJobConfig()
        if params:
            job_config.query_parameters = [
                bigquery.ScalarQueryParameter(name, "STRING", str(value))
                for name, value in params.items() if value is not None
            ]
        query_job = client.query(sql, job_config=job_config, location=os.getenv("BQ_LOCATION"))
        results = [dict(row) for row in query_job.result()]
        logger.info(f"Query returned {len(results)} rows")
        return results
    except Exception as e:
        logger.error(f"Error executing query: {e}")
        raise

def upsert_leave_record(record: Dict[str, Any]) -> str:
    try:
        if 'id' not in record:
            record['id'] = str(uuid.uuid4())
        now = datetime.utcnow()
        if 'created_at' not in record:
            record['created_at'] = now
        record['updated_at'] = now

        logger.info(f"Upserting leave record with ID: {record['id']}")

        merge_sql = f"""
        MERGE `{table_ref()}` AS target
        USING (
            SELECT 
                @id as id,
                @iga_code as iga_code,
                @employee_name as employee_name,
                @base as base,
                @start_date as start_date,
                @end_date as end_date,
                @duration_days as duration_days,
                @comment as comment,
                @status as status,
                @created_by as created_by,
                @created_by_iga as created_by_iga,
                @approved_by as approved_by,
                @approved_by_iga as approved_by_iga,
                @approved_by_date as approved_by_date,
                @rejected_by as rejected_by,
                @rejected_by_iga as rejected_by_iga,
                @rejected_by_date as rejected_by_date,
                @created_at as created_at,
                @updated_at as updated_at
        ) AS source
        ON target.id = source.id
        WHEN MATCHED THEN
            UPDATE SET
                status = source.status,
                comment = COALESCE(source.comment, target.comment),
                approved_by = COALESCE(source.approved_by, target.approved_by),
                approved_by_iga = COALESCE(source.approved_by_iga, target.approved_by_iga),
                approved_by_date = COALESCE(source.approved_by_date, target.approved_by_date),
                rejected_by = COALESCE(source.rejected_by, target.rejected_by),
                rejected_by_iga = COALESCE(source.rejected_by_iga, target.rejected_by_iga),
                rejected_by_date = COALESCE(source.rejected_by_date, target.rejected_by_date),
                updated_at = source.updated_at
        WHEN NOT MATCHED THEN
            INSERT (
                id, iga_code, employee_name, base, start_date, end_date, 
                duration_days, comment, status, 
                created_by, created_by_iga,
                approved_by, approved_by_iga, approved_by_date,
                rejected_by, rejected_by_iga, rejected_by_date,
                created_at, updated_at
            )
            VALUES (
                source.id, source.iga_code, source.employee_name, source.base,
                source.start_date, source.end_date, source.duration_days,
                source.comment, source.status,
                source.created_by, source.created_by_iga,
                source.approved_by, source.approved_by_iga, source.approved_by_date,
                source.rejected_by, source.rejected_by_iga, source.rejected_by_date,
                source.created_at, source.updated_at
            )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", record["id"]),
                bigquery.ScalarQueryParameter("iga_code", "STRING", record["iga_code"]),
                bigquery.ScalarQueryParameter("employee_name", "STRING", record["employee_name"]),
                bigquery.ScalarQueryParameter("base", "STRING", record["base"]),
                bigquery.ScalarQueryParameter("start_date", "DATE", record["start_date"]),
                bigquery.ScalarQueryParameter("end_date", "DATE", record["end_date"]),
                bigquery.ScalarQueryParameter("duration_days", "INT64", record["duration_days"]),
                bigquery.ScalarQueryParameter("comment", "STRING", record.get("comment")),
                bigquery.ScalarQueryParameter("status", "STRING", record["status"]),
                # New audit fields
                bigquery.ScalarQueryParameter("created_by", "STRING", record.get("created_by")),
                bigquery.ScalarQueryParameter("created_by_iga", "STRING", record.get("created_by_iga")),
                bigquery.ScalarQueryParameter("approved_by", "STRING", record.get("approved_by")),
                bigquery.ScalarQueryParameter("approved_by_iga", "STRING", record.get("approved_by_iga")),
                bigquery.ScalarQueryParameter("approved_by_date", "TIMESTAMP", record.get("approved_by_date")),
                bigquery.ScalarQueryParameter("rejected_by", "STRING", record.get("rejected_by")),
                bigquery.ScalarQueryParameter("rejected_by_iga", "STRING", record.get("rejected_by_iga")),
                bigquery.ScalarQueryParameter("rejected_by_date", "TIMESTAMP", record.get("rejected_by_date")),
                # Timestamps
                bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", record["created_at"]),
                bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", record["updated_at"])
            ]
        )

        query_job = client.query(merge_sql, job_config=job_config, location=os.getenv("BQ_LOCATION"))
        query_job.result()
        logger.info(f"Leave record upserted successfully with ID: {record['id']}")
        return record["id"]
    except Exception as e:
        logger.error(f"Error upserting leave record: {e}")
        raise

async def fetch_crew_info(client=None, iga=None, base=None, position=None):
    try:
        if iga:
            logger.info(f"Fetching crew info by IGA: {iga}")
            data = await mcp_tool_call(client, "crew_info_by_iga", {"iga_code": iga})
            logger.info(f"Received {len(data) if data else 0} records for IGA {iga}")
            return data

        if base and position:
            logger.info(f"Fetching crew info by Base: {base}, Position: {position}")
            data = await mcp_tool_call(client, "crew_info_by_base_or_pos", {"base": base, "position": position})
            logger.info(f"Received {len(data) if data else 0} records for Base {base}, Position {position}")
            return data

        logger.warning("Invalid parameters: missing IGA or Base/Position")
        raise ValueError("Must provide either IGA or both Base and Position")

    except Exception as e:
        logger.error(f"Error fetching crew info: {e}")
        raise
