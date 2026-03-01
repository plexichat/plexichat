"""
Admin log management routes.
"""

from fastapi import APIRouter, Request, HTTPException
from typing import List, Optional
from src.api.schemas.admin import LogFileInfo, LogViewResponse
from .utils import check_host_restriction, get_admin_from_token

router = APIRouter()


@router.get("/logs", response_model=List[LogFileInfo])
async def list_admin_logs(request: Request):
    """
    List all available system log files.

    Returns a list of log file names, sizes, and last modification times.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core.admin import logs

    return [LogFileInfo(**log_info) for log_info in logs.list_logs()]


@router.get("/logs/{filename}", response_model=LogViewResponse)
async def read_admin_log(
    request: Request,
    filename: str,
    limit: int = 1000,
    offset: int = 0,
    search: Optional[str] = None,
    level: Optional[str] = None,
):
    """
    Read the contents of a specific log file.

    Supports pagination, filtering by log level, and text searching within the log.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core.admin import logs

    try:
        data = logs.read_log_lines(filename, limit, offset, search, level)
        return LogViewResponse(**data)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Log file not found"}},
        )
