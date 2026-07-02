"""
Admin bot management API routes.

Provides dashboard data and management endpoints for system-wide bot oversight.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request, status

from pydantic import BaseModel

from .utils import check_host_restriction, get_admin_from_token
from src.api.schemas.common import ErrorResponse
from src.core import applications
import utils.logger as logger

router = APIRouter(prefix="/bots", tags=["Admin Bots"])


def _require_admin(request: Request) -> int:
    """Validate admin access for bot admin routes."""
    check_host_restriction(request)
    return get_admin_from_token(request)


class BotStatsResponse(BaseModel):
    """Bot statistics for the admin dashboard."""

    total_applications: int = 0
    total_bots: int = 0
    total_approved: int = 0
    total_pending_requests: int = 0
    total_installations: int = 0
    servers_with_bots: int = 0
    recent_approvals: int = 0
    recent_requests: int = 0


class AdminBotEntry(BaseModel):
    """A bot entry in the admin panel."""

    id: int
    name: str
    owner_id: int
    bot_id: Optional[int] = None
    icon_url: Optional[str] = None
    approved_servers: int = 0
    pending_requests: int = 0
    created_at: int


class AdminBotRequestEntry(BaseModel):
    """A bot request in the admin panel."""

    id: int
    application_id: int
    application_name: str
    server_id: int
    requester_id: int
    reason: Optional[str] = None
    status: str
    created_at: int


@router.get(
    "/stats",
    response_model=BotStatsResponse,
    summary="Get system-wide bot statistics",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_bot_stats(
    request: Request,
):
    """Get bot statistics for the admin dashboard."""
    try:
        _require_admin(request)
        stats = applications.get_admin_bot_stats()
        return BotStatsResponse(**stats)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin bot stats error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.get(
    "/applications",
    response_model=List[AdminBotEntry],
    summary="List all applications for admin",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_list_applications(
    request: Request,
    limit: int = 50,
    offset: int = 0,
):
    """List all applications with bot stats for the admin panel."""
    try:
        _require_admin(request)
        rows = applications.get_admin_bot_applications(limit=limit, offset=offset)
        return [AdminBotEntry(**row) for row in rows]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin bot applications error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.get(
    "/requests",
    response_model=List[AdminBotRequestEntry],
    summary="List all bot requests for admin",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_list_requests(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List bot requests for the admin panel."""
    try:
        _require_admin(request)
        rows = applications.get_admin_bot_requests(
            status_filter=status_filter, limit=limit, offset=offset
        )
        return [AdminBotRequestEntry(**row) for row in rows]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin bot requests error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )
