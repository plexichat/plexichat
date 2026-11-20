"""
Admin audit log routes for querying and exporting audit records.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional, Annotated
from datetime import datetime, timezone
import csv
import io
import json
import time

from pydantic import BeforeValidator

from .utils import check_host_restriction, require_admin_permission
from . import _summary_cache
from ._summary_cache import get_or_compute
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

# The database layer only exposes ``fetch_all`` (no server-side cursors), so a
# single query still had to materialise every exported row at once. Reads are
# now advanced with keyset pagination in bounded pages of this size: peak
# resident rows are O(page size) instead of O(export size). ``id`` is the
# INTEGER PRIMARY KEY and ``created_at`` is indexed, so each seek uses the index
# and the id tiebreaker keeps ordering stable when many events share a ms.
_AUDIT_EXPORT_CHUNK_SIZE = 500

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

# The stats endpoint recomputes eleven COUNT scans over admin_audit_log on
# every poll (three totals + one per action family). Operators poll it from the
# dashboard every few seconds, so collapse those polls into one computation for
# a short window. The host/permission gates below run on every request, before
# the cache is consulted, so authorization is never served from a cached value.
_AUDIT_STATS_TTL_SECONDS = 2.0

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


_CSV_EXPORT_HEADER = [
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

# Snowflake columns stringified in the JSON export, matching the list endpoint
# (a JS Number would round a 64-bit id past 2**53).
_JSON_SNOWFLAKE_COLUMNS = ("admin_id", "target_id", "target_user_id")


def _iter_audit_export_rows(db, first_page, conditions, params, page_size, max_rows):
    """Yield audit rows in bounded, keyset-paginated pages.

    ``first_page`` is fetched eagerly by the caller so a small export still
    issues exactly one data query and DB errors surface before the response
    starts streaming. Later pages advance on ``(created_at, id)`` rather than
    OFFSET: the index on ``created_at`` keeps each seek bounded and the numeric
    primary key breaks ties between events sharing a millisecond.
    """
    page = first_page
    emitted = 0
    while page:
        for row in page:
            if emitted >= max_rows:
                return
            emitted += 1
            yield row
        if len(page) < page_size or emitted >= max_rows:
            return
        last = page[-1]
        keyset = "(created_at < ? OR (created_at = ? AND id < ?))"
        where = " AND ".join([*conditions, keyset]) if conditions else keyset
        page = db.fetch_all(
            "SELECT * FROM admin_audit_log WHERE "
            + where
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            (
                *params,
                last["created_at"],
                last["created_at"],
                last["id"],
                page_size,
            ),
        )


def _iter_audit_csv(rows):
    """Stream CSV one row at a time; never buffer the whole file in memory."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_EXPORT_HEADER)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for log in rows:
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
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def _iter_audit_json(rows):
    """Stream a JSON array one object at a time (bounded memory)."""
    yield '{"logs":['
    first = True
    for log in rows:
        row = dict(log)
        for key in _JSON_SNOWFLAKE_COLUMNS:
            if row.get(key) is not None:
                row[key] = str(row[key])
        payload = {key: redact_secrets(value) for key, value in row.items()}
        if not first:
            yield ","
        yield json.dumps(payload, default=str, ensure_ascii=False)
        first = False
    yield "]}"


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


def _audit_stats_day_start() -> int:
    """UTC midnight in epoch millis -- the audit-stats cache key boundary.

    ``created_at`` is stored as UTC millis (``time.time() * 1000``), so the day
    boundary must be UTC midnight rather than server-local midnight. Computing
    it in one place keeps the route's key and the boot warm-up's key identical,
    so a primed entry is the one the first real poll actually reads.
    """
    return int(
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
        * 1000
    )


def _build_audit_stats(today_start: int) -> dict:
    """Run the eleven COUNT scans that make up the audit-stats summary.

    Kept at module scope so the request path and the boot warm-up prime the
    exact same producer: a second implementation could otherwise drift and
    leave the first poll paying for a cold build the warm-up never covered.
    """
    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")

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


def warm_audit_stats_cache() -> None:
    """Prime today's audit-stats summary off the request path (best-effort).

    The cold hit on ``GET /admin/audit/logs/stats`` issues eleven COUNT scans
    over ``admin_audit_log`` while a request is held open. That work is
    request-invariant and the payload is a system-wide summary with no
    per-admin data, so it is safe to build once on the boot worker thread: the
    host and permission gates still run in the route on every request *before*
    the cache is consulted, and no gate is moved or skipped here. The exact
    ``(namespace, params, producer, ttl)`` tuple used by the route is reused, so
    a warmed entry is indistinguishable from one the route would have cached
    itself. ``_summary_cache.warm`` logs and swallows build failures (e.g. a
    database that is not ready yet); the route still computes on demand.
    """
    today_start = _audit_stats_day_start()
    _summary_cache.warm(
        "audit_stats",
        (today_start,),
        lambda: _build_audit_stats(today_start),
        _AUDIT_STATS_TTL_SECONDS,
    )


@router.get("/audit/logs/stats")
async def get_audit_stats(request: Request):
    """
    Retrieve summary statistics for audit logs (today's totals by action family).
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.audit.read")

    # created_at is stored as UTC millis (time.time() * 1000), so the
    # day boundary must also be UTC midnight, not server-local midnight.
    # Compute it here so the cache key rolls over at midnight rather than
    # serving yesterday's totals for the remainder of the TTL window.
    today_start = _audit_stats_day_start()

    def _load() -> dict:
        return _build_audit_stats(today_start)

    return get_or_compute(
        "audit_stats", (today_start,), _load, _AUDIT_STATS_TTL_SECONDS
    )


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

        # Keyset ordering (numeric primary key as tiebreaker) makes bounded
        # pagination stable even when many events share a millisecond.
        query += " ORDER BY created_at DESC, id DESC"

        page_size = _AUDIT_EXPORT_CHUNK_SIZE
        # Fetch the first page eagerly: small exports still issue a single data
        # query and DB errors surface before the response starts. The remaining
        # pages are pulled lazily in bounded slices while the overall number of
        # exported rows stays capped at _MAX_AUDIT_EXPORT_ROWS.
        first_page = db.fetch_all(query + " LIMIT ?", (*params, page_size))
        rows = _iter_audit_export_rows(
            db,
            first_page,
            list(conditions),
            tuple(params),
            page_size,
            _MAX_AUDIT_EXPORT_ROWS,
        )

        if format == "json":
            return StreamingResponse(
                _iter_audit_json(rows),
                media_type="application/json",
                headers={"Content-Disposition": content_disposition("audit_logs.json")},
            )

        return StreamingResponse(
            _iter_audit_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": content_disposition("audit_logs.csv")},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audit export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
