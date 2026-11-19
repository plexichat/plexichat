"""
Admin audit log routes for querying and exporting audit records.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from typing import Optional, Annotated
from datetime import datetime, timezone
import csv
import io
import time

from pydantic import BeforeValidator

from .utils import check_host_restriction, require_admin_permission
from src.api.schemas.common import ErrorResponse, NullableSnowflakeIDParam
from src.core.security.hardening import content_disposition
from src.core.telemetry.scrubbing import redact_secrets
import utils.logger as logger

router = APIRouter()

# An export had no row limit at all: one request could stream the entire audit
# table into a CSV/JSON buffer. Legitimate exports are bounded by the retention
# window (a year of logs is well under this); cap the read so a single request
# cannot turn an unbounded table scan into an unbounded allocation.
_MAX_AUDIT_EXPORT_ROWS = 10_000

# A filtered query still has to be bounded: the deepest reachable page is
# capped and the widest date window is one retention year. Without an indexed
# predicate the list/export paths would scan the entire table (and the count
# query would too), so every scan is forced to carry one.
_MAX_AUDIT_PAGE = 10_000
_MAX_AUDIT_SCAN_DAYS = 366
_MAX_AUDIT_DATE_RANGE_MS = _MAX_AUDIT_SCAN_DAYS * 24 * 60 * 60 * 1000

# ``date_from``/``date_to`` are Pydantic ``int`` values: a query string such as
# ``?date_from=99999999999999999999`` parses to an arbitrary-precision Python
# int, then raises ``OverflowError`` when SQLite tries to bind it as an INTEGER
# (turning a bad request into a 500). created_at is a non-negative epoch-ms
# value stored in a signed 64-bit column, so reject anything outside that range.
_MIN_AUDIT_TIMESTAMP_MS = 0
_MAX_AUDIT_TIMESTAMP_MS = 2**63 - 1

# Audit rows are untrusted free text: details, actions and user agents can be
# influenced by users whose names/values were recorded in an entry. When an
# exported CSV is opened in Excel/Sheets a cell beginning with one of these
# characters is evaluated as a formula (``=HYPERLINK(...)``, ``=cmd|...``).
# Redaction scrubs secrets but leaves these characters intact, so every text
# cell is rendered as a literal before being written.
_CSV_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    """Render a value as a literal spreadsheet cell, never as a formula."""
    if value is None:
        return ""
    text = "" if isinstance(value, bool) else str(value)
    if not text:
        return text
    probe = text.lstrip(" \t\r\n")
    # Quote when either the raw cell or its first non-whitespace character
    # begins with a spreadsheet formula trigger. Leading tab/CR are unsafe
    # because spreadsheets strip them before evaluating the remainder.
    if text[0] in _CSV_FORMULA_LEADS or (probe and probe[0] in _CSV_FORMULA_LEADS):
        return "'" + text
    return text


def _audit_config() -> dict:
    """Admin-audit query settings from config (best effort)."""
    try:
        import utils.config as config

        return config.get("admin.audit", {}) or {}
    except Exception:
        return {}


def _bounded_scan_days() -> int:
    raw = _audit_config().get("max_scan_days", _MAX_AUDIT_SCAN_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = _MAX_AUDIT_SCAN_DAYS
    return max(1, min(_MAX_AUDIT_SCAN_DAYS, days))


def _validate_date_window(date_from, date_to) -> None:
    """Reject out-of-range, inverted or unbounded-wide date ranges before SQL."""
    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value is not None and not (
            _MIN_AUDIT_TIMESTAMP_MS <= value <= _MAX_AUDIT_TIMESTAMP_MS
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{name} is outside the allowed timestamp range",
            )
    if date_from is None or date_to is None:
        return
    if date_to < date_from:
        raise HTTPException(
            status_code=400, detail="date_to must not precede date_from"
        )
    if date_to - date_from > _MAX_AUDIT_DATE_RANGE_MS:
        raise HTTPException(
            status_code=400, detail="Audit date range exceeds the maximum window"
        )


def _enforce_indexed_scan(conditions, params, date_from=None) -> None:
    """Ensure an indexed, *bounded* predicate exists before any audit scan.

    Callers that supply no filter either get a hard 400 (when
    ``admin.audit.require_indexed_filter`` is enabled) or an automatically
    injected, bounded ``created_at`` lower bound. The latter keeps legacy
    callers working while guaranteeing that no request ever scans the whole
    table; ``idx_admin_audit_created`` (and every other audit index) keeps the
    fan-out proportionate to the requested window.

    A filter being present is not, on its own, a bound. ``date_from=0`` (or any
    pre-retention timestamp) becomes ``created_at >= 0``, which every row
    satisfies, and an upper-only window has no lower bound at all. Both make
    the count and export queries scan the entire table even though an index
    is technically in play. Whenever the caller did not supply a lower bound
    inside the retention window, add the cutoff explicitly so the seek can
    never fan out past the retention period.
    """
    cutoff_ms = int(time.time() * 1000) - _bounded_scan_days() * 86400 * 1000
    has_bounded_lower = date_from is not None and date_from >= cutoff_ms

    if not conditions and not has_bounded_lower:
        if bool(_audit_config().get("require_indexed_filter", False)):
            raise HTTPException(
                status_code=400,
                detail="An indexed filter is required for audit scans",
            )

    if has_bounded_lower:
        return

    if not conditions:
        # Legacy unfiltered callers keep their existing bound-parameter shape.
        conditions.append("created_at >= ?")
        params.append(cutoff_ms)
    else:
        # Keep every caller-supplied parameter untouched (including an explicit
        # ``date_from=0``) and add the retention cutoff as a server-computed
        # integer literal instead. This preserves the query shape the filtered
        # endpoints already expose while making the index seek genuinely
        # bounded; the literal is never caller text.
        conditions.append(f"created_at >= {cutoff_ms}")


def _coerce_nullable_int(value):
    """Coerce ``null``/empty query values for optional int params to None."""
    if (
        value is None
        or value == ""
        or (isinstance(value, str) and value.strip().lower() == "null")
    ):
        return None
    return value


NullableInt = Annotated[Optional[int], BeforeValidator(_coerce_nullable_int)]


def _escape_like(text: str) -> str:
    """Escape LIKE metacharacters so the filter is matched literally.

    Parameterization blocks SQL injection but not ``%``/``_`` acting as
    wildcards, so a bare ``%`` action-family filter would silently match every
    action. ``docs/admin-hardening.md`` requires ``%``, ``_`` and ``\\`` to be
    treated literally; the escape character is declared via ``ESCAPE '\\'``.
    """
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get(
    "/audit/logs",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid filter or pagination parameter",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Database not available"},
    },
)
async def get_audit_logs(
    request: Request,
    action_family: Optional[str] = Query(
        None, max_length=64, description="Filter by action family"
    ),
    status: Optional[str] = Query(None, max_length=32, description="Filter by status"),
    admin_id: Annotated[
        NullableSnowflakeIDParam, Query(description="Filter by admin ID")
    ] = None,
    target_type: Optional[str] = Query(
        None, max_length=64, description="Filter by target type"
    ),
    date_from: Annotated[NullableInt, Query(description="Start timestamp (ms)")] = None,
    date_to: Annotated[NullableInt, Query(description="End timestamp (ms)")] = None,
    page: int = Query(1, ge=1, le=_MAX_AUDIT_PAGE, description="Page number"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
):
    """
    Retrieve paginated audit log entries with optional filters.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.audit.read")

    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")

        query = "SELECT * FROM admin_audit_log"
        conditions = []
        params = []

        if action_family:
            conditions.append("action LIKE ? ESCAPE '\\'")
            params.append(f"{_escape_like(action_family)}%")

        if status:
            conditions.append("status = ?")
            params.append(status)

        if admin_id is not None:
            conditions.append("admin_id = ?")
            params.append(admin_id)

        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)

        # ``_coerce_nullable_int`` maps null/empty to None, so a present 0 is a
        # real bound (epoch) and must not be dropped as falsy.
        if date_from is not None:
            conditions.append("created_at >= ?")
            params.append(date_from)

        if date_to is not None:
            conditions.append("created_at <= ?")
            params.append(date_to)

        _validate_date_window(date_from, date_to)
        _enforce_indexed_scan(conditions, params, date_from)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        # Build a separate count query with the same WHERE conditions
        count_query = "SELECT COUNT(*) as total FROM admin_audit_log"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        total = db.fetch_one(count_query, params)["total"]

        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, (page - 1) * per_page])

        logs = db.fetch_all(query, params)

        formatted_logs = []
        for log in logs:
            formatted_logs.append(
                {
                    # ``id`` is the audit row's INTEGER PRIMARY KEY rowid and
                    # stays numeric; ``admin_id`` is the acting admin's 64-bit
                    # snowflake, so it is emitted as a decimal string.
                    "id": log["id"],
                    "admin_id": str(log["admin_id"])
                    if log.get("admin_id") is not None
                    else None,
                    "action": log["action"],
                    "target_type": log["target_type"],
                    # target_id is a user/server/channel snowflake whenever the
                    # target is one of those; emit it as a decimal string so a
                    # JS Number cannot round it above 2^53. It can be absent for
                    # non-entity actions, so keep None as None rather than
                    # stringifying it to "None".
                    "target_id": str(log["target_id"])
                    if log.get("target_id") is not None
                    else None,
                    "details": redact_secrets(log.get("details")),
                    "ip_address": redact_secrets(log.get("ip_address")),
                    "user_agent": redact_secrets(log.get("user_agent")),
                    "status": log["status"],
                    "created_at": log["created_at"],
                    # created_at is UTC millis; render UTC so the list, the
                    # stats day boundary, and the CSV export agree on the
                    # instant regardless of the server's local zone.
                    "timestamp": datetime.fromtimestamp(
                        log["created_at"] / 1000, tz=timezone.utc
                    ).isoformat()
                    if log.get("created_at")
                    else None,
                }
            )

        return {
            "logs": formatted_logs,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audit logs retrieval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/audit/logs/stats")
async def get_audit_stats(request: Request):
    """
    Retrieve summary statistics for audit logs (today's totals by action family).
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.audit.read")

    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")

        # created_at is stored as UTC millis (time.time() * 1000), so the
        # day boundary must also be UTC midnight, not server-local midnight.
        today_start = int(
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )

        stats = {
            "total_today": db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ?",
                (today_start,),
            )["count"],
            "success_today": db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ? AND status = 'success'",
                (today_start,),
            )["count"],
            "failed_today": db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ? AND status = 'failed'",
                (today_start,),
            )["count"],
            "by_action_family": {},
        }

        families = [
            "login",
            "user",
            "admin",
            "config",
            "approval",
            "security",
            "plexijoin",
            "license",
        ]
        for family in families:
            count = db.fetch_one(
                "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ? AND action LIKE ?",
                (today_start, f"{family}%"),
            )["count"]
            if count > 0:
                stats["by_action_family"][family] = count

        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audit stats retrieval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/audit/logs/export",
    responses={
        200: {
            "description": "The exported audit log file",
            "content": {
                "text/csv": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "object"}},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid filter parameter"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Database not available"},
    },
)
async def export_audit_logs(
    request: Request,
    # Bound the filter strings exactly like the paginated list endpoint does;
    # the export path had left them unrestricted, so an arbitrarily long
    # LIKE/equality value reached the query and the CSV header row.
    action_family: Optional[str] = Query(None, max_length=64),
    status: Optional[str] = Query(None, max_length=32),
    admin_id: Annotated[NullableSnowflakeIDParam, Query()] = None,
    target_type: Optional[str] = Query(None, max_length=64),
    date_from: Annotated[NullableInt, Query()] = None,
    date_to: Annotated[NullableInt, Query()] = None,
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    """
    Export audit logs as CSV or JSON.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.audit.read")

    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")

        query = "SELECT * FROM admin_audit_log"
        conditions = []
        params = []

        if action_family:
            conditions.append("action LIKE ? ESCAPE '\\'")
            params.append(f"{_escape_like(action_family)}%")

        if status:
            conditions.append("status = ?")
            params.append(status)

        if admin_id is not None:
            conditions.append("admin_id = ?")
            params.append(admin_id)

        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)

        # ``_coerce_nullable_int`` maps null/empty to None, so a present 0 is a
        # real bound (epoch) and must not be dropped as falsy.
        if date_from is not None:
            conditions.append("created_at >= ?")
            params.append(date_from)

        if date_to is not None:
            conditions.append("created_at <= ?")
            params.append(date_to)

        _validate_date_window(date_from, date_to)
        _enforce_indexed_scan(conditions, params, date_from)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        # Hard row cap: an export may never materialise more than
        # _MAX_AUDIT_EXPORT_ROWS rows, regardless of the filters supplied.
        params.append(_MAX_AUDIT_EXPORT_ROWS)
        query += " LIMIT ?"

        logs = db.fetch_all(query, params)

        if format == "json":
            from fastapi.responses import JSONResponse

            # Snowflake columns (the acting admin and the entity target) are
            # stringified in the JSON export too, matching the list endpoint.
            export_rows = []
            for log in logs:
                row = dict(log)
                for key in ("admin_id", "target_id", "target_user_id"):
                    if row.get(key) is not None:
                        row[key] = str(row[key])
                # Every exported column is untrusted free text; scrub the whole
                # row so a token in details/UA cannot leave via the JSON export.
                export_rows.append(
                    {key: redact_secrets(value) for key, value in row.items()}
                )

            return JSONResponse(
                content={"logs": export_rows},
                headers={"Content-Disposition": content_disposition("audit_logs.json")},
            )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "ID",
                "Admin ID",
                "Action",
                "Target Type",
                "Target ID",
                "Details",
                "IP Address",
                "User Agent",
                "Status",
                "Timestamp",
            ]
        )

        for log in logs:
            timestamp = (
                datetime.fromtimestamp(
                    log["created_at"] / 1000, tz=timezone.utc
                ).isoformat()
                if log.get("created_at")
                else ""
            )
            writer.writerow(
                [
                    _csv_cell(redact_secrets(log["id"])),
                    _csv_cell(redact_secrets(log["admin_id"])),
                    _csv_cell(redact_secrets(log["action"])),
                    _csv_cell(redact_secrets(log.get("target_type", ""))),
                    _csv_cell(redact_secrets(log.get("target_id", ""))),
                    _csv_cell(redact_secrets(log.get("details", ""))),
                    _csv_cell(redact_secrets(log.get("ip_address", ""))),
                    _csv_cell(redact_secrets(log.get("user_agent", ""))),
                    _csv_cell(redact_secrets(log["status"])),
                    _csv_cell(timestamp),
                ]
            )

        output.seek(0)

        from fastapi.responses import StreamingResponse

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": content_disposition("audit_logs.csv")},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audit export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
