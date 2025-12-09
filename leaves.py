from datetime import datetime
from typing import Dict, Any, List, Optional
import uuid
import os
import json
from google.cloud import bigquery
from logger_config import get_logger
from tools import mcp_tool_call

logger = get_logger("urti_leaves")
client = bigquery.Client(project=os.getenv("GCP_PROJECT_ID"))

def table_ref() -> str:
    return f"{os.getenv('GCP_PROJECT_ID')}.{os.getenv('BQ_DATASET')}.{os.getenv('BQ_TABLE')}"

def query_rows(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        job_config = bigquery.QueryJobConfig()
        if params:
            query_parameters = []
            for name, value in params.items():
                if value is not None:
                    if name in ['page_size', 'offset']:
                        query_parameters.append(bigquery.ScalarQueryParameter(name, "INT64", int(value)))
                    else:
                        query_parameters.append(bigquery.ScalarQueryParameter(name, "STRING", str(value)))
            job_config.query_parameters = query_parameters
        query_job = client.query(sql, job_config=job_config, location=os.getenv("BQ_LOCATION"))
        results = [dict(row) for row in query_job.result()]
        logger.info(f"Query returned {len(results)} rows")
        return results
    except Exception as e:
        logger.error(f"Error executing query: {e}")
        raise

def query_paginated_leaves(page: int = 1, page_size: int = 20, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Query leaves with pagination support"""
    try:
        # Build WHERE clause from filters
        where_conditions = []
        params = {}
        is_search_active = False
        
        if filters:
            if filters.get('iga_code'):
                where_conditions.append('iga_code = @iga_code')
                params['iga_code'] = filters['iga_code']
            if filters.get('status'):
                where_conditions.append('status = @status')
                params['status'] = filters['status']
            if filters.get('base'):
                where_conditions.append('base = @base')
                params['base'] = filters['base']
            if filters.get('search'):
                where_conditions.append('LOWER(employee_name) LIKE LOWER(@search)')
                params['search'] = f"%{filters['search']}%"
                is_search_active = True
            if filters.get('date_from'):
                where_conditions.append('DATE(created_at) >= @date_from')
                params['date_from'] = filters['date_from']
            if filters.get('date_to'):
                where_conditions.append('DATE(created_at) <= @date_to')
                params['date_to'] = filters['date_to']
        
        where_clause = 'WHERE ' + ' AND '.join(where_conditions) if where_conditions else ''
        
        # Count total records
        count_sql = f"SELECT COUNT(*) as total FROM `{table_ref()}` {where_clause}"
        count_result = query_rows(count_sql, params)
        total_records = count_result[0]['total'] if count_result else 0
        
        # Get status counts (excluding status filter for summary cards)
        status_where_conditions = [c for c in where_conditions if 'status' not in c.lower()]
        status_where_clause = 'WHERE ' + ' AND '.join(status_where_conditions) if status_where_conditions else ''
        status_params = {k: v for k, v in params.items() if k != 'status'}
        
        status_count_sql = f"""
        SELECT 
            status,
            COUNT(*) as count
        FROM `{table_ref()}`
        {status_where_clause}
        GROUP BY status
        """
        status_counts_result = query_rows(status_count_sql, status_params)
        status_counts = {
            'pending': 0,
            'approved': 0,
            'rejected': 0
        }
        for row in status_counts_result:
            status_key = row['status'].lower()
            if status_key in status_counts:
                status_counts[status_key] = row['count']
        
        # If search is active, return all results without pagination
        if is_search_active:
            data_sql = f"""
            SELECT * FROM `{table_ref()}`
            {where_clause}
            ORDER BY created_at DESC
            """
            records = query_rows(data_sql, params)
            decoded_records = decode_json_fields(records)
            
            return {
                'data': decoded_records,
                'pagination': {
                    'page': 1,
                    'page_size': total_records,
                    'total_records': total_records,
                    'total_pages': 1,
                    'has_next': False,
                    'has_prev': False
                },
                'status_counts': status_counts
            }
        
        # Normal pagination when no search
        offset = (page - 1) * page_size
        data_sql = f"""
        SELECT * FROM `{table_ref()}`
        {where_clause}
        ORDER BY created_at DESC
        LIMIT {page_size} OFFSET {offset}
        """
        
        records = query_rows(data_sql, params)
        decoded_records = decode_json_fields(records)
        
        return {
            'data': decoded_records,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_records': total_records,
                'total_pages': (total_records + page_size - 1) // page_size,
                'has_next': page * page_size < total_records,
                'has_prev': page > 1
            },
            'status_counts': status_counts
        }
    except Exception as e:
        logger.error(f"Error in paginated query: {e}")
        raise

def check_overlapping_leaves(iga_code: str, start_date: str, end_date: str, exclude_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Check for overlapping leave records for the same IGA code"""
    try:
        sql = f"""
        SELECT id, iga_code, start_date, end_date, status
        FROM `{table_ref()}`
        WHERE iga_code = @iga_code
        AND status != 'REJECTED'
        AND (
            (start_date <= @start_date AND end_date >= @start_date) OR
            (start_date <= @end_date AND end_date >= @end_date) OR
            (start_date >= @start_date AND end_date <= @end_date)
        )
        """
        
        params = {
            'iga_code': iga_code,
            'start_date': start_date,
            'end_date': end_date
        }
        
        if exclude_id:
            sql += " AND id != @exclude_id"
            params['exclude_id'] = exclude_id
            
        return query_rows(sql, params)
    except Exception as e:
        logger.error(f"Error checking overlapping leaves: {e}")
        raise

def upsert_leave_record(record: Dict[str, Any]) -> str:
    try:
        record.setdefault('id', str(uuid.uuid4()))
        now = datetime.utcnow()
        record.setdefault('created_at', now)
        record['updated_at'] = now

        # Check for overlapping leaves only for new records or status updates
        if record.get('status') != 'REJECTED':
            overlapping = check_overlapping_leaves(
                record['iga_code'], 
                record['start_date'], 
                record['end_date'],
                record['id']
            )
            if overlapping:
                def format_date(date_str):
                    try:
                        return datetime.strptime(str(date_str), "%Y-%m-%d").strftime("%d/%m/%Y")
                    except:
                        return str(date_str)
                
                overlap_details = [f"ID: {leave['id']}, Dates: {format_date(leave['start_date'])} to {format_date(leave['end_date'])}" for leave in overlapping]
                raise ValueError(f"Leave dates overlap with existing records for IGA {record['iga_code']}: {'; '.join(overlap_details)}")

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
                @status_updated_by as status_updated_by,
                @created_at as created_at,
                @updated_at as updated_at
        ) AS source
        ON target.id = source.id
        WHEN MATCHED THEN
            UPDATE SET
                status = source.status,
                created_by = COALESCE(target.created_by, source.created_by),
                status_updated_by = source.status_updated_by,
                comment = COALESCE(source.comment, target.comment),
                updated_at = source.updated_at
        WHEN NOT MATCHED THEN
            INSERT (id, iga_code, employee_name, base, start_date, end_date,
                    duration_days, comment, status, created_by, status_updated_by, created_at, updated_at)
            VALUES (source.id, source.iga_code, source.employee_name, source.base,
                    source.start_date, source.end_date, source.duration_days,
                    source.comment, source.status, source.created_by, source.status_updated_by, source.created_at, source.updated_at)
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
                bigquery.ScalarQueryParameter("created_by", "STRING", json.dumps(record.get("created_by")) if record.get("created_by") else None),
                bigquery.ScalarQueryParameter("status_updated_by", "STRING", json.dumps(record.get("status_updated_by")) if record.get("status_updated_by") else None),
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
        raise ValueError("Missing IGA or Base/Position")
    except Exception as e:
        logger.error(f"Error fetching crew info: {e}")
        raise
def decode_json_fields(leaves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for leave in leaves:
        for field in ['created_by', 'status_updated_by']:
            if leave.get(field) and isinstance(leave[field], str):
                try:
                    leave[field] = json.loads(leave[field])
                except Exception:
                    leave[field] = None
    return leaves
