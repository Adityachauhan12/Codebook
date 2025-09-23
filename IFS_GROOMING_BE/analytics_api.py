@router.get("/analytics")
async def analytics_summary(
    startDate: str = Query(..., description="YYYY-MM-DD"),
    endDate: str = Query(..., description="YYYY-MM-DD"),
    iga: Optional[str] = None,
    crew: Optional[str] = None,
    limitDays: int = 45
):
    start = _parse_date(startDate)
    end = _parse_date(endDate)
    if (end - start).days > limitDays:
        end = start + timedelta(days=limitDays)
    f = Filters(start=start, end=end, iga=iga, crew=crew)
    records, _ = fetch_assessments(f, limit=10_000, offset=0, order_by="timestamp_asc")
    summary = compute_analytics(records, f)
    summary["kpis"]["total_liveliness_success"] = fetch_liveliness_success(f)
    return {
        "range": {"startDate": startDate, "endDate": endDate},
        "filters": {"iga": iga, "crew": crew},
        **summary
    }

@router.get("/tables")
async def analytics_tables(
    startDate: str = Query(..., description="YYYY-MM-DD"),
    endDate: str = Query(..., description="YYYY-MM-DD"),
    iga: Optional[str] = None,
    crew: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    orderBy: str = "timestamp_desc"
):
    start = _parse_date(startDate)
    end = _parse_date(endDate)
    f = Filters(start=start, end=end, iga=iga, crew=crew)
    rows, total = fetch_assessments(f, limit=limit, offset=offset, order_by=orderBy)

    # Remove GCS paths and empty parsed blocks
    for r in rows:
        r.pop("image_gcs_path", None)
        r.pop("result_text_gcs_path", None)
        r.pop("structured_gcs_path", None)
        if r.get("parsed") and all(not v for v in r["parsed"].values()):
            r.pop("parsed")

    issues = {}
    for r in rows:
        parsed = r.get("parsed") or {}
        issues_block = (parsed.get("issues_found") or "").strip()
        if issues_block:
            for line in issues_block.splitlines():
                line = line.strip().lstrip("-•* ").strip()
                if line:
                    issues[line] = issues.get(line, 0) + 1

    tickets = read_recent_tickets(limit=100)
    for t in tickets:
        t.pop("image_gcs_path", None)
        t.pop("result_text_gcs_path", None)
        t.pop("structured_gcs_path", None)
        t.pop("video_gcs_path", None)
        t.pop("result_gcs_path", None)

    return {
        "range": {"startDate": startDate, "endDate": endDate},
        "filters": {"iga": iga, "crew": crew},
        "assessments": {"rows": rows, "total": total},
        "issues_rollup": [{"issue": k, "count": v} for k, v in sorted(issues.items(), key=lambda x: x[1], reverse=True)],
        "recent_tickets": tickets
    }
