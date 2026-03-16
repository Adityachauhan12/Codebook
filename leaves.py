# tools/clms_sql_query.py
import os
import json
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple

import pyodbc

log = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Status names in M_LMS_StatusType.StatusType (adjust if your values differ)
STATUS_PENDING = "Pending"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"

# Diagnostics: print driver list once if env is set
LOG_ODBC_DRIVERS_ONCE = os.getenv("CLMS_LOG_ODBC_DRIVERS", "").lower() == "true"
_logged_drivers_once = False


# =============================================================================
# Common helpers (drivers, connections, JSON, guards)
# =============================================================================

def _maybe_log_available_drivers_once():
    global _logged_drivers_once
    if _logged_drivers_once or not LOG_ODBC_DRIVERS_ONCE:
        return
    try:
        log.info("ODBC drivers (server-side): %s", pyodbc.drivers())
    except Exception:
        pass
    _logged_drivers_once = True


def _candidate_conn_strings_read() -> List[str]:
    """
    Read-only connection candidates.
    If CLMS_ODBC_CONN is set, use it as-is (even if not read-only).
    Else build candidates with Driver 18 then 17 (or pinned by CLMS_ODBC_DRIVER),
    and include ApplicationIntent=ReadOnly.
    """
    full = os.getenv("CLMS_ODBC_CONN")
    if full:
        return [full]

    server = os.getenv("CLMS_DB_SERVER", "")
    dbname = os.getenv("CLMS_DB_NAME", "CLMS")
    user = os.getenv("CLMS_DB_USER", "")
    pwd = os.getenv("CLMS_DB_PASSWORD", "")
    if not (server and dbname and user and pwd):
        raise RuntimeError(
            "CLMS DB credentials are not set. "
            "Set CLMS_DB_SERVER, CLMS_DB_NAME, CLMS_DB_USER, CLMS_DB_PASSWORD "
            "or set CLMS_ODBC_CONN."
        )

    pinned = os.getenv("CLMS_ODBC_DRIVER")
    drivers = [pinned] if pinned else [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
    ]

    candidates: List[str] = []
    for d in drivers:
        parts = [
            f"Driver={{{d}}};",
            f"Server={server};",
            f"Database={dbname};",
            f"Uid={user};",
            f"Pwd={pwd};",
            "Encrypt=yes;",
            "TrustServerCertificate=no;",
            "Connection Timeout=60;",
            "Authentication=SqlPassword;",
            "ApplicationIntent=ReadOnly;",
        ]
        candidates.append("".join(parts))
    return candidates


def _candidate_conn_strings_write() -> List[str]:
    """
    Write-capable connection candidates.
    If CLMS_ODBC_CONN is set, use it as-is.
    Else build candidates with Driver 18 then 17 (or pinned by CLMS_ODBC_DRIVER),
    without ApplicationIntent=ReadOnly.
    """
    full = os.getenv("CLMS_ODBC_CONN")
    if full:
        return [full]

    server = os.getenv("CLMS_DB_SERVER", "")
    dbname = os.getenv("CLMS_DB_NAME", "CLMS")
    user = os.getenv("CLMS_DB_USER", "")
    pwd = os.getenv("CLMS_DB_PASSWORD", "")
    if not (server and dbname and user and pwd):
        raise RuntimeError(
            "CLMS DB credentials are not set. "
            "Set CLMS_DB_SERVER, CLMS_DB_NAME, CLMS_DB_USER, CLMS_DB_PASSWORD "
            "or set CLMS_ODBC_CONN."
        )

    pinned = os.getenv("CLMS_ODBC_DRIVER")
    drivers = [pinned] if pinned else [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
    ]

    candidates: List[str] = []
    for d in drivers:
        parts = [
            f"Driver={{{d}}};",
            f"Server={server};",
            f"Database={dbname};",
            f"Uid={user};",
            f"Pwd={pwd};",
            "Encrypt=yes;",
            "TrustServerCertificate=no;",
            "Connection Timeout=60;",
            "Authentication=SqlPassword;",
        ]
        candidates.append("".join(parts))
    return candidates


def _connect_any(read_only: bool) -> pyodbc.Connection:
    """
    Try driver candidates (18 -> 17) or CLMS_ODBC_CONN, return a live connection.
    autocommit=False (we control transactions).
    """
    _maybe_log_available_drivers_once()
    last_err: Optional[Exception] = None
    candidates = _candidate_conn_strings_read() if read_only else _candidate_conn_strings_write()
    for conn_str in candidates:
        try:
            return pyodbc.connect(conn_str, autocommit=False)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        msg = str(last_err)
        if "Data source name not found" in msg or "driver" in msg.lower():
            raise RuntimeError(
                "No suitable ODBC driver found (tried 18 then 17). "
                "Install Microsoft ODBC driver 18 or 17, or set CLMS_ODBC_DRIVER/CLMS_ODBC_CONN."
            )
        raise last_err
    raise RuntimeError("Unknown error establishing SQL connection.")


def _json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    try:
        return str(obj)
    except Exception:
        return None


def _is_select_only(sql: str) -> bool:
    """
    Very conservative guard: a single SELECT or WITH...SELECT only.
    No UPDATE/DELETE/INSERT/MERGE/DDL/SP/EXEC; no multi-statements.
    """
    if not sql or not isinstance(sql, str):
        return False
    s = sql.strip().strip(";")
    u = s.upper()

    blocked = [
        "UPDATE ", "DELETE ", "INSERT ", "MERGE ", "ALTER ", "DROP ",
        "CREATE ", "TRUNCATE ", "EXEC ", "EXECUTE ", "GRANT ", "REVOKE ",
        "BACKUP ", "RESTORE ", "SP_", "XP_"
    ]
    if any(b in u for b in blocked):
        return False
    if not (u.startswith("SELECT ") or u.startswith("WITH ")):
        return False
    if ";" in s[:-1]:
        return False
    return True


# =============================================================================
# Read-only: SELECT
# =============================================================================

def _run_sql_query(query: str, limit: int = 1000, as_csv: bool = False) -> Dict[str, Any]:
    if not query or not query.strip():
        return {"error": {"code": "EMPTY_QUERY", "message": "No query provided"}}
    if not _is_select_only(query):
        return {"error": {"code": "UNSAFE_QUERY", "message": "Only a single SELECT (or WITH...SELECT) is allowed."}}

    q = query.strip().rstrip(";")
    upper = q.lstrip().upper()

    # Add TOP N if caller didn't include TOP/OFFSET/FETCH
    if limit and " TOP " not in upper.split("\n")[0] and " OFFSET " not in upper and " FETCH " not in upper:
        if upper.startswith("SELECT "):
            q = q.replace("SELECT", f"SELECT TOP {int(limit)}", 1)
        elif upper.startswith("WITH "):
            idx = q.upper().find("SELECT ")
            if idx > -1:
                q = q[:idx] + q[idx:].replace("SELECT", f"SELECT TOP {int(limit)}", 1)

    log.info("CLMS read query requested | limit=%s", limit)
    log.debug("Query preview: %s", q[:200])

    try:
        with _connect_any(read_only=True) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
                except Exception:
                    pass

                cur.execute(q)
                columns = [c[0] for c in cur.description] if cur.description else []
                if not columns:
                    return {"result": "[]"}

                if as_csv:
                    rows = cur.fetchall()
                    out = [",".join(columns)]
                    for r in rows:
                        out.append(",".join("" if v is None else str(v) for v in r))
                    return {"result": "\n".join(out)}
                else:
                    rows = cur.fetchall()
                    out_list = []
                    for r in rows:
                        out_list.append({columns[i]: r[i] for i in range(len(columns))})
                    return {"result": json.dumps(out_list, indent=2, default=_json_serializer)}
    except pyodbc.Error as e:
        msg = str(e)
        if "login failed" in msg.lower():
            return {"error": {"code": "AUTH_FAILED", "message": "SQL authentication failed."}}
        if "server" in msg.lower() and "not found" in msg.lower():
            return {"error": {"code": "SERVER_NOT_FOUND", "message": "Check server and connectivity."}}
        if "timeout" in msg.lower():
            return {"error": {"code": "TIMEOUT", "message": "DB timeout. Reduce result size or simplify the query."}}
        return {"error": {"code": "SQL_ERROR", "message": msg}}
    except Exception as e:
        return {"error": {"code": "UNEXPECTED", "message": str(e)}}


# =============================================================================
# Write-enabled: CLMS actions (validate/create/approve/adjust)
# =============================================================================

def _check_role_or_throw(actor_id: Optional[str], action: str) -> None:
    """
    RBAC placeholder—tie this to M_UMS_User/M_UMS_Role/M_UMS_UserRole if required.
    Raise PermissionError if the actor is not allowed for 'action'.
    """
    return


def _get_status_id(conn: pyodbc.Connection, status_name: str) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TOP 1 StatusTypeID FROM M_LMS_StatusType WHERE UPPER(StatusType) = UPPER(?)",
            (status_name,)
        )
        row = cur.fetchone()
        return int(row[0]) if row else None


def _crew_exists_and_active(conn: pyodbc.Connection, crew_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT TOP 1 ISNULL(Active, 1) FROM M_CLMS_Crew WHERE CrewID = ?", (crew_id,))
        row = cur.fetchone()
        return bool(row[0]) if row else False


def _get_active_leave_year_for_crew(conn: pyodbc.Connection, crew_id: int) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT TOP 1 LeaveYearId
            FROM T_Crew_LeaveYear
            WHERE Crewid = ? AND ISNULL(IsActive, 1) = 1
            ORDER BY CreatedDtm DESC
        """, (crew_id,))
        row = cur.fetchone()
        return int(row[0]) if row else None


def _leave_type_exists(conn: pyodbc.Connection, leave_type_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM M_LMS_LeaveType WHERE LeaveTypeID = ?", (leave_type_id,))
        return cur.fetchone() is not None


def _date_overlap_exists(conn: pyodbc.Connection, crew_id: int, from_dt: datetime, to_dt: datetime) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(1)
            FROM T_CLMS_LeaveReqMaster m
            JOIN M_LMS_StatusType st ON st.StatusTypeID = m.StatusTypeID
            WHERE m.CrewID = ?
              AND m.FromDT <= ? AND m.ToDt >= ?
              AND UPPER(st.StatusType) NOT IN ('REJECTED','CANCELLED','CANCELED')
        """, (crew_id, to_dt, from_dt))
        row = cur.fetchone()
        return int(row[0]) > 0


def _get_balance(conn: pyodbc.Connection, crew_id: int, leave_type_id: int, leave_year_id: int) -> Optional[float]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT TOP 1 Balance
            FROM M_CLMS_LeaveBalance
            WHERE CrewID = ? AND LeaveTypeID = ? AND LeaveYearId = ?
            ORDER BY BalanceID DESC
        """, (crew_id, leave_type_id, leave_year_id))
        row = cur.fetchone()
        return float(row[0]) if row else None


def _update_balance(conn: pyodbc.Connection, crew_id: int, leave_type_id: int,
                    leave_year_id: int, delta: float) -> Tuple[float, float]:
    """
    Applies delta to the current balance (can be negative to deduct).
    Returns (old_balance, new_balance).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT TOP 1 Balance
            FROM M_CLMS_LeaveBalance
            WHERE CrewID = ? AND LeaveTypeID = ? AND LeaveYearId = ?
            ORDER BY BalanceID DESC
        """, (crew_id, leave_type_id, leave_year_id))
        row = cur.fetchone()
        if not row:
            raise ValueError("Balance record not found for crew/year/type.")
        old_bal = float(row[0])
        new_bal = old_bal + float(delta)
        if new_bal < 0:
            raise ValueError("Insufficient balance for the requested operation.")

        cur.execute("""
            UPDATE M_CLMS_LeaveBalance
            SET Balance = ?
            WHERE CrewID = ? AND LeaveTypeID = ? AND LeaveYearId = ?
        """, (new_bal, crew_id, leave_type_id, leave_year_id))
        return old_bal, new_bal


def _insert_scheduler_balance_audit(conn: pyodbc.Connection, crew_id: int,
                                    old_sl: Optional[float], new_sl: Optional[float],
                                    old_urti: Optional[float], new_urti: Optional[float],
                                    leave_year_id: int, is_revoked: int = 0) -> None:
    """
    Writes an audit row into CrewBalanceUpdateByScheduler.
    If a field is not applicable, pass None.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO CrewBalanceUpdateByScheduler
                (crewID, OldSLBalance, updatedSLBalance, OldURTIBalance, updatedURTIBalance,
                 LWD, IsSchedulerUpdate, EligibleMonths, LeaveYearID, UpdatedTime, IsRevoked, CreatedDtm)
            VALUES (?, ?, ?, ?, ?, NULL, 1, NULL, ?, GETDATE(), ?, GETDATE())
        """, (crew_id, old_sl, new_sl, old_urti, new_urti, leave_year_id, is_revoked))


def _insert_log(conn: pyodbc.Connection, process_name: str, description: str,
                status: str, error_detail: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO L_LMS_Log (ProcessName, ProcessType, Frequency, Description, CreatedBy, CreatedDtm)
            VALUES (?, ?, ?, ?, ?, GETDATE());
            SELECT SCOPE_IDENTITY();
        """, (process_name, "MCP", "OnDemand", description[:4000], "mcp"))
        log_id = int(cur.fetchone()[0])

        cur.execute("""
            INSERT INTO L_LMS_LogDetail (LogID, LogDate, Description, ErrorDetail, Status)
            VALUES (?, GETDATE(), ?, ?, ?)
        """, (log_id, description[:4000], (error_detail or "")[:4000], status))


def _compute_days_inclusive(from_dt: datetime, to_dt: datetime) -> int:
    return (to_dt.date() - from_dt.date()).days + 1


# =============================================================================
# Single-service class (read + write) with a dispatcher
# =============================================================================

class ClmsService:
    """
    Single entry point for CLMS:
      - action='sql_query'                -> SELECT-only query
      - action='validate_leave_request'   -> validations (no writes)
      - action='create_leave_request'     -> master + detail (Pending)
      - action='approve_leave_request'    -> approve/reject + balance update
      - action='adjust_leave_balance'     -> direct adjustment + audit
    Returns a uniform envelope: {action, success, data|None, warnings:[], error|None}
    """

    # ---- READ ----
    def sql_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        query = payload.get("query")
        limit = int(payload.get("limit", 1000))
        as_csv = bool(payload.get("as_csv", False))
        return _run_sql_query(query, limit=limit, as_csv=as_csv)

    # ---- VALIDATE ----
    def validate_leave_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            crew_id = int(payload["crew_id"])
            leave_type_id = int(payload["leave_type_id"])
            from_dt = str(payload["from_dt"])
            to_dt = str(payload["to_dt"])
            leave_year_id = payload.get("leave_year_id")
            if leave_year_id is not None:
                leave_year_id = int(leave_year_id)

            from_parsed = datetime.fromisoformat(from_dt)
            to_parsed = datetime.fromisoformat(to_dt)
            if from_parsed > to_parsed:
                return {"success": False, "error": {"code": "INVALID_DATES", "message": "from_dt must be <= to_dt"}}

            with _connect_any(read_only=True) as conn:
                if not _crew_exists_and_active(conn, crew_id):
                    return {"success": False, "error": {"code": "CREW_NOT_ACTIVE", "message": "Crew not found or inactive."}}
                if not _leave_type_exists(conn, leave_type_id):
                    return {"success": False, "error": {"code": "LEAVE_TYPE_INVALID", "message": "Leave type not found."}}
                if leave_year_id is None:
                    leave_year_id = _get_active_leave_year_for_crew(conn, crew_id)
                    if leave_year_id is None:
                        return {"success": False, "error": {"code": "LEAVE_YEAR_NOT_FOUND", "message": "Active leave year not mapped."}}
                if _date_overlap_exists(conn, crew_id, from_parsed, to_parsed):
                    return {"success": False, "error": {"code": "DATE_OVERLAP", "message": "Existing request overlaps these dates."}}

                bal = _get_balance(conn, crew_id, leave_type_id, leave_year_id)
                if bal is None:
                    return {"success": False, "error": {"code": "BALANCE_NOT_FOUND", "message": "Balance row missing."}}

                days = _compute_days_inclusive(from_parsed, to_parsed)
                if days > bal:
                    return {"success": False, "error": {"code": "INSUFFICIENT_BALANCE",
                                                       "message": f"Requested {days} exceeds balance {bal}",
                                                       "details": {"requested_days": days, "balance": bal, "leave_year_id": leave_year_id}}}

                return {"success": True, "requested_days": days, "balance": bal, "leave_year_id": leave_year_id}
        except KeyError as ke:
            return {"success": False, "error": {"code": "MISSING_FIELD", "message": f"Missing payload field: {ke}"}}
        except Exception as e:
            log.exception("validate_leave_request error")
            return {"success": False, "error": {"code": "UNEXPECTED", "message": str(e)}}

    # ---- CREATE ----
    def create_leave_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            crew_id = int(payload["crew_id"])
            leave_type_id = int(payload["leave_type_id"])
            from_dt = str(payload["from_dt"])
            to_dt = str(payload["to_dt"])
            comments = str(payload.get("comments", "") or "")
            leave_year_id = payload.get("leave_year_id")
            actor_id = payload.get("actor_id")
            if leave_year_id is not None:
                leave_year_id = int(leave_year_id)

            _check_role_or_throw(actor_id, "create_leave_request")

            from_parsed = datetime.fromisoformat(from_dt)
            to_parsed = datetime.fromisoformat(to_dt)
            if from_parsed > to_parsed:
                return {"success": False, "error": {"code": "INVALID_DATES", "message": "from_dt must be <= to_dt"}}

            with _connect_any(read_only=False) as conn:
                try:
                    cur = conn.cursor()

                    # Basic validations
                    if not _crew_exists_and_active(conn, crew_id):
                        return {"success": False, "error": {"code": "CREW_NOT_ACTIVE", "message": "Crew not found or inactive."}}
                    if not _leave_type_exists(conn, leave_type_id):
                        return {"success": False, "error": {"code": "LEAVE_TYPE_INVALID", "message": "Leave type not found."}}
                    if leave_year_id is None:
                        leave_year_id = _get_active_leave_year_for_crew(conn, crew_id)
                        if leave_year_id is None:
                            return {"success": False, "error": {"code": "LEAVE_YEAR_NOT_FOUND", "message": "Active leave year not mapped."}}
                    if _date_overlap_exists(conn, crew_id, from_parsed, to_parsed):
                        return {"success": False, "error": {"code": "DATE_OVERLAP", "message": "Existing request overlaps these dates."}}

                    bal = _get_balance(conn, crew_id, leave_type_id, leave_year_id)
                    if bal is None:
                        return {"success": False, "error": {"code": "BALANCE_NOT_FOUND", "message": "Balance row missing."}}

                    days = _compute_days_inclusive(from_parsed, to_parsed)
                    if days <= 0:
                        return {"success": False, "error": {"code": "ZERO_DAYS", "message": "Invalid day computation."}}

                    status_pending_id = _get_status_id(conn, STATUS_PENDING)
                    if status_pending_id is None:
                        return {"success": False, "error": {"code": "STATUS_NOT_FOUND", "message": f"'{STATUS_PENDING}' not configured."}}

                    # Insert Master
                    cur.execute("""
                        INSERT INTO T_CLMS_LeaveReqMaster
                            (CrewID, RequestedDate, LeaveTypeid, StatusTypeID, FromDT, ToDt,
                             WLNO, CrewComment, Remarks, ActionBy, ActionDate, CreatedDtm, GndCode)
                        VALUES (?, GETDATE(), ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, GETDATE(), NULL);
                        SELECT SCOPE_IDENTITY();
                    """, (crew_id, leave_type_id, status_pending_id, from_parsed, to_parsed, comments))
                    leave_detail_id = int(cur.fetchone()[0])

                    # Insert Detail (single line covering full window)
                    cur.execute("""
                        INSERT INTO T_CLMS_LeaveReqDetail
                            (LeaveDetailID, LeaveTypeID, NoOfLeaves, Balance, CurrentLeave, FromDt, ToDt)
                        VALUES (?, ?, ?, NULL, NULL, ?, ?)
                    """, (leave_detail_id, leave_type_id, float(days), from_parsed, to_parsed))

                    _insert_log(conn, "clms_create_leave_request",
                                f"Created leave request {leave_detail_id} for crew {crew_id}, {days} day(s).",
                                "SUCCESS")
                    conn.commit()

                    return {"success": True,
                            "leave_detail_id": leave_detail_id,
                            "crew_id": crew_id,
                            "leave_type_id": leave_type_id,
                            "from_dt": from_dt,
                            "to_dt": to_dt,
                            "requested_days": days,
                            "status": STATUS_PENDING,
                            "leave_year_id": leave_year_id}
                except Exception as txe:
                    conn.rollback()
                    _insert_log(conn, "clms_create_leave_request",
                                f"Failed to create leave for crew {crew_id}", "ERROR", str(txe))
                    log.exception("create_leave_request TX error")
                    return {"success": False, "error": {"code": "TX_FAILED", "message": str(txe)}}
        except KeyError as ke:
            return {"success": False, "error": {"code": "MISSING_FIELD", "message": f"Missing payload field: {ke}"}}
        except Exception as e:
            log.exception("create_leave_request error")
            return {"success": False, "error": {"code": "UNEXPECTED", "message": str(e)}}

    # ---- APPROVE/REJECT ----
    def approve_leave_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            leave_detail_id = int(payload["leave_detail_id"])
            decision = str(payload["decision"]).strip().lower()
            approver_id = payload.get("approver_id")
            remarks = str(payload.get("remarks", "") or "")

            if decision not in ("approve", "reject"):
                return {"success": False, "error": {"code": "INVALID_DECISION", "message": "Use 'approve' or 'reject'."}}

            _check_role_or_throw(approver_id, "approve_leave_request")

            with _connect_any(read_only=False) as conn:
                try:
                    cur = conn.cursor()
                    # Fetch Master
                    cur.execute("""
                        SELECT m.CrewID, m.LeaveTypeid, m.FromDT, m.ToDt, m.StatusTypeID
                        FROM T_CLMS_LeaveReqMaster m
                        WHERE m.LeaveDetailID = ?
                    """, (leave_detail_id,))
                    row = cur.fetchone()
                    if not row:
                        return {"success": False, "error": {"code": "NOT_FOUND", "message": "Leave request not found."}}

                    crew_id, leave_type_id, from_dt, to_dt, status_id = row

                    status_pending_id = _get_status_id(conn, STATUS_PENDING)
                    if status_pending_id is None:
                        return {"success": False, "error": {"code": "STATUS_NOT_FOUND", "message": f"'{STATUS_PENDING}' not configured."}}
                    if int(status_id) != int(status_pending_id):
                        return {"success": False, "error": {"code": "INVALID_STATE", "message": "Only Pending can be approved/rejected."}}

                    days = _compute_days_inclusive(from_dt, to_dt)
                    target_status = STATUS_APPROVED if decision == "approve" else STATUS_REJECTED
                    target_status_id = _get_status_id(conn, target_status)
                    if target_status_id is None:
                        return {"success": False, "error": {"code": "STATUS_NOT_FOUND", "message": f"'{target_status}' not configured."}}

                    leave_year_id = _get_active_leave_year_for_crew(conn, int(crew_id))
                    if leave_year_id is None:
                        return {"success": False, "error": {"code": "LEAVE_YEAR_NOT_FOUND", "message": "Active leave year not mapped."}}

                    old_balance = None
                    new_balance = None

                    if decision == "approve":
                        old_balance, new_balance = _update_balance(conn, int(crew_id), int(leave_type_id),
                                                                   int(leave_year_id), delta=-float(days))
                        # Optional: SL/URTI audit row; pass fields as applicable
                        _insert_scheduler_balance_audit(conn, int(crew_id),
                                                        old_sl=old_balance, new_sl=new_balance,
                                                        old_urti=None, new_urti=None,
                                                        leave_year_id=int(leave_year_id), is_revoked=0)

                    # Update status + approver
                    cur.execute("""
                        UPDATE T_CLMS_LeaveReqMaster
                        SET StatusTypeID = ?, ActionBy = ?, ActionDate = GETDATE(), Remarks = ?
                        WHERE LeaveDetailID = ?
                    """, (int(target_status_id), approver_id, remarks, int(leave_detail_id)))

                    _insert_log(conn, "clms_approve_leave_request",
                                f"{target_status} leave {leave_detail_id} by {approver_id}.",
                                "SUCCESS")
                    conn.commit()

                    return {"success": True,
                            "leave_detail_id": int(leave_detail_id),
                            "crew_id": int(crew_id),
                            "leave_type_id": int(leave_type_id),
                            "from_dt": from_dt.isoformat() if hasattr(from_dt, "isoformat") else str(from_dt),
                            "to_dt": to_dt.isoformat() if hasattr(to_dt, "isoformat") else str(to_dt),
                            "decision": target_status,
                            "leave_year_id": int(leave_year_id),
                            "balance_before": old_balance,
                            "balance_after": new_balance}
                except Exception as txe:
                    conn.rollback()
                    _insert_log(conn, "clms_approve_leave_request",
                                f"Failed to {decision} {leave_detail_id}", "ERROR", str(txe))
                    log.exception("approve_leave_request TX error")
                    return {"success": False, "error": {"code": "TX_FAILED", "message": str(txe)}}
        except KeyError as ke:
            return {"success": False, "error": {"code": "MISSING_FIELD", "message": f"Missing payload field: {ke}"}}
        except Exception as e:
            log.exception("approve_leave_request error")
            return {"success": False, "error": {"code": "UNEXPECTED", "message": str(e)}}

    # ---- ADJUST BALANCE ----
    def adjust_leave_balance(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            crew_id = int(payload["crew_id"])
            leave_type_id = int(payload["leave_type_id"])
            leave_year_id = int(payload["leave_year_id"])
            delta = float(payload["delta"])
            reason = str(payload.get("reason", "") or "")
            actor_id = payload.get("actor_id")

            _check_role_or_throw(actor_id, "adjust_leave_balance")

            with _connect_any(read_only=False) as conn:
                try:
                    old_balance, new_balance = _update_balance(conn, crew_id, leave_type_id, leave_year_id, delta)

                    _insert_scheduler_balance_audit(conn, crew_id,
                                                    old_sl=old_balance, new_sl=new_balance,
                                                    old_urti=None, new_urti=None,
                                                    leave_year_id=leave_year_id,
                                                    is_revoked=1 if delta > 0 else 0)

                    _insert_log(conn, "clms_adjust_leave_balance",
                                f"Adjusted balance for crew {crew_id}, type {leave_type_id} by {delta}. Reason: {reason}",
                                "SUCCESS")
                    conn.commit()

                    return {"success": True,
                            "crew_id": crew_id,
                            "leave_type_id": leave_type_id,
                            "leave_year_id": leave_year_id,
                            "delta": float(delta),
                            "balance_before": old_balance,
                            "balance_after": new_balance}
                except Exception as txe:
                    conn.rollback()
                    _insert_log(conn, "clms_adjust_leave_balance",
                                f"Failed to adjust balance for crew {crew_id}", "ERROR", str(txe))
                    log.exception("adjust_leave_balance TX error")
                    return {"success": False, "error": {"code": "TX_FAILED", "message": str(txe)}}
        except KeyError as ke:
            return {"success": False, "error": {"code": "MISSING_FIELD", "message": f"Missing payload field: {ke}"}}
        except Exception as e:
            log.exception("adjust_leave_balance error")
            return {"success": False, "error": {"code": "UNEXPECTED", "message": str(e)}}

    # ---- DISPATCHER ----
    def handle(self, action: str, payload: Optional[Dict[str, Any]] = None,
               idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Single entry point for the MCP tool.
        Returns a uniform envelope: {action, success, data|None, warnings:[], error|None}
        """
        payload = payload or {}
        try:
            if action == "sql_query":
                result = self.sql_query(payload)
                if "error" in result:
                    return {"action": action, "success": False, "data": None, "warnings": [], "error": result["error"]}
                return {"action": action, "success": True, "data": result, "warnings": [], "error": None}

            if action == "validate_leave_request":
                res = self.validate_leave_request(payload)
                return {"action": action, "success": bool(res.get("success")), "data": res if res.get("success") else None,
                        "warnings": [], "error": None if res.get("success") else res.get("error")}

            if action == "create_leave_request":
                res = self.create_leave_request(payload)
                return {"action": action, "success": bool(res.get("success")), "data": res if res.get("success") else None,
                        "warnings": [], "error": None if res.get("success") else res.get("error")}

            if action == "approve_leave_request":
                res = self.approve_leave_request(payload)
                return {"action": action, "success": bool(res.get("success")), "data": res if res.get("success") else None,
                        "warnings": [], "error": None if res.get("success") else res.get("error")}

            if action == "adjust_leave_balance":
                res = self.adjust_leave_balance(payload)
                return {"action": action, "success": bool(res.get("success")), "data": res if res.get("success") else None,
                        "warnings": [], "error": None if res.get("success") else res.get("error")}

            return {"action": action, "success": False, "data": None, "warnings": [],
                    "error": {"code": "UNKNOWN_ACTION", "message": f"Unsupported action: {action}"}}
        except Exception as e:
            log.exception("clms.handle unexpected error")
            return {"action": action, "success": False, "data": None, "warnings": [],
                    "error": {"code": "UNEXPECTED", "message": str(e)}}
