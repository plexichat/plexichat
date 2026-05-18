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
        stats = BotStatsResponse()

        # Count total applications
        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(*) as count FROM app_applications"
        )
        stats.total_applications = rows[0]["count"] if rows else 0

        # Count applications with bots
        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(*) as count FROM app_applications WHERE bot_id IS NOT NULL"
        )
        stats.total_bots = rows[0]["count"] if rows else 0

        # Count approved bots
        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(*) as count FROM app_approved_bots WHERE status = 'approved'"
        )
        stats.total_approved = rows[0]["count"] if rows else 0

        # Count pending requests
        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(*) as count FROM app_bot_requests WHERE status = 'pending'"
        )
        stats.total_pending_requests = rows[0]["count"] if rows else 0

        # Count total installations
        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(*) as count FROM app_installations"
        )
        stats.total_installations = rows[0]["count"] if rows else 0

        # Count servers with bots
        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(DISTINCT server_id) as count FROM app_approved_bots WHERE status = 'approved'"
        )
        stats.servers_with_bots = rows[0]["count"] if rows else 0

        # Recent activity (last 7 days)
        week_ago = applications._get_manager()._get_timestamp() - 604800
        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(*) as count FROM app_approved_bots WHERE installed_at >= ?",
            (week_ago,),
        )
        stats.recent_approvals = rows[0]["count"] if rows else 0

        rows = applications._get_manager()._db.fetch_all(
            "SELECT COUNT(*) as count FROM app_bot_requests WHERE created_at >= ?",
            (week_ago,),
        )
        stats.recent_requests = rows[0]["count"] if rows else 0

        return stats
    except Exception as e:
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
        if limit > 100:
            limit = 100

        rows = applications._get_manager()._db.fetch_all(
            """SELECT a.id, a.name, a.owner_id, a.bot_id, a.icon_url, a.created_at,
                      (SELECT COUNT(*) FROM app_approved_bots ab WHERE ab.application_id = a.id AND ab.status = 'approved') as approved_count,
                      (SELECT COUNT(*) FROM app_bot_requests br WHERE br.application_id = a.id AND br.status = 'pending') as pending_count
               FROM app_applications a
               ORDER BY a.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )

        return [
            AdminBotEntry(
                id=row["id"],
                name=row["name"],
                owner_id=row["owner_id"],
                bot_id=row["bot_id"],
                icon_url=row["icon_url"],
                approved_servers=row["approved_count"] or 0,
                pending_requests=row["pending_count"] or 0,
                created_at=row["created_at"],
            )
            for row in rows
        ]
    except Exception as e:
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
        if limit > 100:
            limit = 100

        conditions = []
        params = []
        if status_filter:
            conditions.append("br.status = ?")
            params.append(status_filter)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        rows = applications._get_manager()._db.fetch_all(
            f"""SELECT br.id, br.application_id, a.name as app_name, br.server_id,
                      br.requester_id, br.reason, br.status, br.created_at
               FROM app_bot_requests br
               JOIN app_applications a ON br.application_id = a.id{where_clause}
               ORDER BY br.created_at DESC
               LIMIT ? OFFSET ?""",
            tuple(params),
        )

        return [
            AdminBotRequestEntry(
                id=row["id"],
                application_id=row["application_id"],
                application_name=row["app_name"],
                server_id=row["server_id"],
                requester_id=row["requester_id"],
                reason=row["reason"],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )
