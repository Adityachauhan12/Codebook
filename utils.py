@app.get("/api/leave/listLeaves")
def list_leaves(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    base: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    iga_code: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None)
):
    """
    Returns:
      {
        "data": [...],
        "pagination": { page, page_size, total_records, total_pages, has_next, has_prev },
        "status_counts": { pending, approved, rejected }
      }
    All WHERE filters are applied consistently to counts, except that status_counts
    deliberately ignores any incoming `status` filter so the cards can show the
    distribution within the filtered set.
    """
    filters = {
        k: v for k, v in {
            "base": base,
            "status": (status.lower() if status else None),  # normalize for consistency
            "iga_code": iga_code,
            "search": search,
            "date_from": date_from,
            "date_to": date_to
        }.items() if v
    }
 
    logger.info(f"Listing leaves with filters: {filters}, page: {page}, page_size: {page_size}")
    try:
        # This already builds identical WHEREs for data and total_records
        # and a status_counts query that drops the 'status' filter.
        result = query_paginated_leaves(page, page_size, filters)
 
        # 🔒 Defensive: If any upstream rows have weird case, normalize before returning
        # (keeps UI labels consistent; does NOT change DB)
        fixed_counts = {
            'pending': int(result.get('status_counts', {}).get('pending', 0)),
            'approved': int(result.get('status_counts', {}).get('approved', 0)),
            'rejected': int(result.get('status_counts', {}).get('rejected', 0)),
        }
        result['status_counts'] = fixed_counts
 
        # Return the original object shape expected by Admin analytics cards
        return result
    except Exception as e:
        logger.error(f"Error listing leaves: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/leave/list_leaves_analytics")
def list_leaves_analytics(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    base: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    iga_code: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None)
):
    filters = {k: v for k, v in {"base": base, "status": status, "iga_code": iga_code, "search": search, "date_from": date_from, "date_to": date_to}.items() if v}
    logger.info(f"Listing leaves analytics with filters: {filters}, page: {page}, page_size: {page_size}")
    try:
        result = query_paginated_leaves(page, page_size, filters)
        logger.info(f"Found {result['pagination']['total_records']} total leave records (analytics)")
        return result
    except Exception as e:
        logger.error(f"Error listing leaves analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.patch("/api/leave/ApproveRejectLeave/{leave_id}")
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
    
    
@app.get("/api/leave/getEmployeeLeaves/{iga_code}")
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
    
@app.get("/api/leave/leaveSummary")
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
