"""
Admin log management routes.
"""

import os
from fastapi import APIRouter, Request, HTTPException, Query
from typing import List, Optional
from src.api.schemas.admin import LogFileInfo, LogViewResponse
from src.api.schemas.common import ErrorResponse
from .utils import (
    check_host_restriction,
    # Retained for test monkeypatch compatibility; the routes now authenticate
    # and authorize together through require_admin_permission.
    get_admin_from_token,  # noqa: F401
    require_admin_permission,
)

router = APIRouter()


def _validate_log_filename(filename: str) -> str:
    """Confine a log filename to a single, safe path component.

    ``read_log_lines`` repeats these checks defensively, but validating at the
    route boundary means a traversal attempt never reaches the filesystem
    layer at all. Backslashes are rejected as well as slashes so a Windows
    style ``..\\..\\`` sequence cannot become a path separator if the server
    runs on a platform (or a mount) that treats it as one.
    """
    if (
        not filename
        or os.path.basename(filename) != filename
        or ".." in filename
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
    ):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid filename"}},
        )
    return filename


@router.get(
    "/logs",
    response_model=List[LogFileInfo],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_admin_logs(request: Request):
    """
    List all available system log files.

    Returns a list of log file names, sizes, and last modification times.
    """
    check_host_restriction(request)
    # Reading log metadata exposes instance internals. Authentication alone is
    # not enough: a valid token carrying an unrelated scope must not enumerate
    # the log directory, so require the dedicated ``logs.read`` scope.
    require_admin_permission(request, "logs.read")
    from src.core.admin import logs

    return [LogFileInfo(**log_info) for log_info in logs.list_logs()]


@router.get(
    "/logs/{filename}",
    response_model=LogViewResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid filename"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Log file not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def read_admin_log(
    request: Request,
    filename: str,
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0, le=1_000_000),
    search: Optional[str] = Query(None, max_length=200),
    level: Optional[str] = Query(None, max_length=20),
):
    """
    Read the contents of a specific log file.

    Supports pagination, filtering by log level, and text searching within the log.
    """
    check_host_restriction(request)
    # Reading log contents (which may include secrets that survived redaction
    # at the source) must require the ``logs.read`` scope, not merely a valid
    # admin token with any scope.
    require_admin_permission(request, "logs.read")

    sanitized = _validate_log_filename(filename)

    from src.core.admin import logs

    try:
        data = logs.read_log_lines(sanitized, limit, offset, search, level)
        return LogViewResponse(**data)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Log file not found"}},
        )
