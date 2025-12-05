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
    except Exception as e:
        logger.error(f"Error creating leave request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/listLeaves")
def list_leaves(
    base: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    iga_code: Optional[str] = Query(None),
    date: Optional[str] = Query(None)
):
    sql = f"SELECT * FROM `{table_ref()}` WHERE 1=1"
    params = {}
    if base:
        sql += " AND base = @base"
        params["base"] = base
    if status:
        sql += " AND status = @status"
        params["status"] = status
    if iga_code:
        sql += " AND iga_code = @iga_code"
        params["iga_code"] = iga_code
    if date:
        sql += " AND DATE(created_at) = @date"
        params["date"] = date
    sql += " ORDER BY created_at DESC"

    logger.info(f"Listing leaves with filters: {params}")
    try:
        leaves = query_rows(sql, params)
        logger.info(f"Found {len(leaves)} leave records")
        return decode_json_fields(leaves)
    except Exception as e:
        logger.error(f"Error listing leaves: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/list_leaves_analytics")
def list_leaves_analytics(
    base: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    iga_code: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    duration_days: Optional[int] = Query(None),
    employee_name: Optional[str] = Query(None)
):
    sql = f"SELECT * FROM `{table_ref()}` WHERE 1=1"
    params = {}
    if base:
        sql += " AND base = @base"
        params["base"] = base
    if status:
        sql += " AND status = @status"
        params["status"] = status
    if iga_code:
        sql += " AND iga_code = @iga_code"
        params["iga_code"] = iga_code
    if start_date:
        sql += " AND start_date >= @start_date"
        params["start_date"] = start_date
    if end_date:
        sql += " AND end_date <= @end_date"
        params["end_date"] = end_date
    if duration_days is not None:
        sql += " AND duration_days = @duration_days"
        params["duration_days"] = duration_days
    if employee_name:
        sql += " AND employee_name = @employee_name"
        params["employee_name"] = employee_name
    sql += " ORDER BY created_at DESC"

    logger.info(f"Listing leaves analytics with filters: {params}")
    try:
        leaves = query_rows(sql, params)
        logger.info(f"Found {len(leaves)} leave records (analytics)")
        return decode_json_fields(leaves)
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
def get_employee_leaves(iga_code: str):
    logger.info(f"Fetching leaves for employee {iga_code}")
    sql = f"SELECT * FROM `{table_ref()}` WHERE iga_code = @iga_code ORDER BY created_at DESC"
    try:
        leaves = query_rows(sql, {"iga_code": iga_code})
        logger.info(f"Found {len(leaves)} leaves for employee {iga_code}")
        return decode_json_fields(leaves)
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
