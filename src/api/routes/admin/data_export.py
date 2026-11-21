"""
Admin DSAR data-export routes.

Implements the documented admin approval workflow
(docs/admin/data-export.md):
  GET    /data-export                     list requests   (data_export.read)
  GET    /data-export/{request_id}        request detail  (data_export.read)
  POST   /data-export/{request_id}/approve                (data_export.process)
  POST   /data-export/{request_id}/deny                    (data_export.process)
  POST   /data-export/{request_id}/generate                (data_export.process)

Without these routes every user request deadlocked in 'pending' forever
because require_admin_review defaults to true.
"""

from src.core.security.hardening import parse_id

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
)
from src.core.events.snowflakes import stringify_snowflake_ids
from .utils import check_host_restriction, require_admin_permission
from src.core.dsar import (
    get_admin_requests,
    approve_request,
    deny_request,
    generate_manual,
)
from src.core.dsar.security import InvalidExportInput, validate_export_id

router = APIRouter()

# Explicit response allowlist. ``dsar_requests`` rows are NEVER serialised
# verbatim: storage_path (server filesystem/object layout) and checksum /
# metadata internals must not leak through the admin API.
REQUEST_RESPONSE_COLUMNS = (
    "id",
    "user_id",
    "status",
    "requested_at",
    "completed_at",
    "expires_at",
    "format",
    "storage_backend",
    "file_size_bytes",
    "admin_id",
    "denial_reason",
    "error_message",
)

# The detail read used ``SELECT *``, pulling ``storage_path`` (server
# filesystem layout), ``checksum`` and ``metadata`` into the process on every
# view even though ``_serialize_request`` never emits them. Project exactly the
# allowlisted columns at the SQL boundary: fewer bytes off disk and no needless
# copy of internal-only fields into the admin response path. Derived from the
# allowlist so the projection can never drift from what the route serializes.
_REQUEST_DETAIL_SQL = (
    "SELECT " + ", ".join(REQUEST_RESPONSE_COLUMNS) + " FROM dsar_requests"
)


def _serialize_request(row: dict) -> dict:
    record = dict(row or {})
    serialized = {key: record.get(key) for key in REQUEST_RESPONSE_COLUMNS}
    # ``dsar_requests`` ids are 64-bit snowflakes (``generate_snowflake_id``),
    # so ``id``/``user_id``/``admin_id`` must cross the wire as decimal
    # strings — a JS Number rounds them past 2**53 and the admin UI would
    # then address the wrong request. Timestamps stay numeric.
    return stringify_snowflake_ids(serialized)


class DenyBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


def _get_request_or_404(request_id: int) -> dict:
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    row = db.fetch_one(_REQUEST_DETAIL_SQL + " WHERE id = ?", (request_id,))
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Request not found"}},
        )
    return dict(row)


def _handle_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": 400, "message": str(exc)}},
    )


def _handle_mutation_error(request_id: int, exc: ValueError) -> HTTPException:
    """Map a DSAR mutation error to HTTP, preserving the 404 contract.

    The DSAR core raises ``ValueError("DSAR request <id> not found")`` for a
    missing row. The old route pre-flighted every mutation with a separate
    ``SELECT *`` just to turn that into a 404; detecting it here keeps the same
    status codes while removing one database round trip per mutation.
    """
    if f"DSAR request {request_id} not found" in str(exc):
        return HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Request not found"}},
        )
    return _handle_value_error(exc)


@router.get(
    "/data-export",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def list_data_exports(
    request: Request,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1000000),
):
    """List all DSAR export requests with optional status filter."""
    check_host_restriction(request)
    require_admin_permission(request, "data_export.read")
    try:
        # Project only the columns the response actually emits. Without this
        # the list read was ``SELECT *`` and dragged ``storage_path``,
        # ``checksum`` and ``metadata`` (server filesystem/object layout and
        # integrity internals) across the DB boundary for every row, only for
        # ``_serialize_request`` to discard them. Same allowlist as the detail
        # read, so the projection cannot drift from the response shape.
        items = get_admin_requests(
            status=status,
            limit=limit,
            offset=offset,
            columns=REQUEST_RESPONSE_COLUMNS,
        )
    except Exception as e:
        logger.error(f"Admin data-export listing failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    has_more = len(items) == limit
    return {
        "items": [_serialize_request(item) for item in items],
        "has_more": has_more,
    }


@router.get("/data-export/{request_id}")
async def get_data_export(request: Request, request_id: SnowflakeIDParam):
    """View detailed information about a specific DSAR request."""
    check_host_restriction(request)
    require_admin_permission(request, "data_export.read")
    try:
        req_id = parse_id(request_id, "request_id")
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request ID"}},
        )
    return _serialize_request(_get_request_or_404(req_id))


@router.post("/data-export/{request_id}/approve")
async def approve_data_export(request: Request, request_id: SnowflakeIDParam):
    """Approve a pending DSAR request so the harvester can process it."""
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "data_export.process")
    # The sibling GET binds this path id with ``parse_id`` and GENERATE with
    # ``validate_export_id`` (also a parse_id gate). These destructive handlers
    # used a bare ``int()`` instead: Python's int accepts Unicode decimal
    # homoglyphs ("٤٢" -> 42), PEP 515 separators ("1_2" -> 12), signs,
    # bools/floats and unbounded integers, so the same path segment was
    # reinterpreted as a different id here than at every other step of the
    # approval workflow. Bind through the one canonical ASCII snowflake gate
    # before any mutation (fail closed).
    try:
        req_id = parse_id(request_id, "request_id")
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request ID"}},
        )
    try:
        await run_in_threadpool(approve_request, req_id, int(admin_id))
    except ValueError as e:
        raise _handle_mutation_error(req_id, e)
    except Exception as e:
        logger.error(
            f"Admin data-export approve failed for {req_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    logger.info(f"Admin {admin_id} approved data-export request {req_id}")
    return SuccessResponse(success=True, message="Request approved")


@router.post("/data-export/{request_id}/deny")
async def deny_data_export(
    request: Request, request_id: SnowflakeIDParam, body: DenyBody
):
    """Deny a pending DSAR request with a reason."""
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "data_export.process")
    # Same boundary gate as approve/get/generate: never let bare ``int()``
    # reinterpret a homoglyph/separator/signed/oversized id at this destructive
    # endpoint. See approve_data_export for the full rationale.
    try:
        req_id = parse_id(request_id, "request_id")
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request ID"}},
        )
    try:
        await run_in_threadpool(deny_request, req_id, int(admin_id), body.reason)
    except ValueError as e:
        raise _handle_mutation_error(req_id, e)
    except Exception as e:
        logger.error(
            f"Admin data-export deny failed for {req_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    logger.info(f"Admin {admin_id} denied data-export request {req_id}")
    return SuccessResponse(success=True, message="Request denied")


@router.post("/data-export/{request_id}/generate")
async def generate_data_export(request: Request, request_id: SnowflakeIDParam):
    """Manually trigger export generation for a pending/approved request."""
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "data_export.process")
    # Fail closed on the export id before doing any generation work: the core
    # additionally binds the fetched row to this id (IDOR defence).
    try:
        req_id = validate_export_id(request_id, "request id")
    except InvalidExportInput:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request ID"}},
        )
    try:
        result = await run_in_threadpool(generate_manual, req_id, int(admin_id))
    except ValueError as e:
        raise _handle_mutation_error(req_id, e)
    except Exception as e:
        logger.error(
            f"Admin data-export generation failed for {request_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    if result is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Export generation failed"}},
        )
    return {"success": True, "request": _serialize_request(result)}
