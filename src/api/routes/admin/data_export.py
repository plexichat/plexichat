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

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
)
from .utils import require_admin_permission
from src.core.dsar import (
    get_admin_requests,
    approve_request,
    deny_request,
    generate_manual,
)

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


def _serialize_request(row: dict) -> dict:
    record = dict(row or {})
    return {key: record.get(key) for key in REQUEST_RESPONSE_COLUMNS}


class DenyBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


def _get_request_or_404(request_id: int) -> dict:
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    row = db.fetch_one("SELECT * FROM dsar_requests WHERE id = ?", (request_id,))
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
    limit: int = 50,
    offset: int = 0,
):
    """List all DSAR export requests with optional status filter."""
    require_admin_permission(request, "data_export.read")
    try:
        items = get_admin_requests(status=status, limit=limit, offset=offset)
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
    require_admin_permission(request, "data_export.read")
    try:
        req_id = int(request_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request ID"}},
        )
    return _serialize_request(_get_request_or_404(req_id))


@router.post("/data-export/{request_id}/approve")
async def approve_data_export(request: Request, request_id: SnowflakeIDParam):
    """Approve a pending DSAR request so the harvester can process it."""
    admin_id = require_admin_permission(request, "data_export.process")
    _get_request_or_404(int(request_id))
    try:
        await run_in_threadpool(approve_request, int(request_id), int(admin_id))
    except ValueError as e:
        raise _handle_value_error(e)
    except Exception as e:
        logger.error(
            f"Admin data-export approve failed for {request_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    logger.info(f"Admin {admin_id} approved data-export request {request_id}")
    return SuccessResponse(success=True, message="Request approved")


@router.post("/data-export/{request_id}/deny")
async def deny_data_export(
    request: Request, request_id: SnowflakeIDParam, body: DenyBody
):
    """Deny a pending DSAR request with a reason."""
    admin_id = require_admin_permission(request, "data_export.process")
    _get_request_or_404(int(request_id))
    try:
        await run_in_threadpool(
            deny_request, int(request_id), int(admin_id), body.reason
        )
    except ValueError as e:
        raise _handle_value_error(e)
    except Exception as e:
        logger.error(
            f"Admin data-export deny failed for {request_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    logger.info(f"Admin {admin_id} denied data-export request {request_id}")
    return SuccessResponse(success=True, message="Request denied")


@router.post("/data-export/{request_id}/generate")
async def generate_data_export(request: Request, request_id: SnowflakeIDParam):
    """Manually trigger export generation for a pending/approved request."""
    admin_id = require_admin_permission(request, "data_export.process")
    _get_request_or_404(int(request_id))
    try:
        result = await run_in_threadpool(
            generate_manual, int(request_id), int(admin_id)
        )
    except ValueError as e:
        raise _handle_value_error(e)
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
