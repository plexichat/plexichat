"""
Admin API routes.

Provides admin-only endpoints for server management.
Host-restricted by default to localhost only.

SECURITY WARNING: Disabling host_restriction allows ANYONE to access the admin
panel and view potentially sensitive user data including feedback, telemetry,
and system statistics. Only disable this if you have other security measures
in place (VPN, firewall, reverse proxy auth, etc.)
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.responses import HTMLResponse
from typing import List, Optional, Union
import time

import src.api as api
import utils.config as config
import utils.logger as logger
from src.api.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    OTPVerifyRequest,
    TicketStatusUpdate,
    InternalNoteCreate,
    HashReportReviewRequest,
    ManualBlockHashRequest,
    TicketResponse,
    NoteResponse,
    AdminDashboardResponse,
    TelemetryStatsResponse,
    TelemetryHistoryResponse,
    TelemetryResetResponse,
    HashReportResponse,
    HashReportCountsResponse,
    BlockedHashResponse,
    BlockUserRequest,
    BlockedUserResponse,
    UserTierUpdate,
    UserSearchResponse,
    UserSearchListResponse,
    UserDetailsResponse,
    UserTierUpdateResponse,
    UserBadgeUpdateResponse,
    HashReportReviewResponse,
    BlockHashResponse,
    BlockUserResponse,
    TelemetryExportResponse,
    AvailableTierInfo,
    AvailableTiersResponse,
    AvailableBadgesResponse,
    TelemetryEndpointStat,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["Admin Management"])


def _check_host_restriction(request: Request) -> None:
    """Check if client IP is allowed to access admin UI."""
    # Bypass all restrictions for secure self-test requests or localhost
    is_selftest = request.scope.get("state", {}).get("is_selftest", False)
    # Check request.state as well for robustness
    if not is_selftest:
        is_selftest = getattr(request.state, "is_selftest", False)
        
    client_ip = request.client.host if request.client else "unknown"
    is_local = client_ip in ("127.0.0.1", "localhost", "::1")

    if is_selftest or is_local:
        if is_selftest:
            logger.info(f"Admin host restriction bypass (is_selftest=True): {request.method} {request.url.path}")
        return

    admin_config = config.get("admin_ui", {})

    if not admin_config.get("enabled", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Not found"}}
        )

    host_restriction = admin_config.get("host_restriction", {})
    if host_restriction.get("enabled", True):
        allowed_hosts = host_restriction.get("allowed_hosts", ["127.0.0.1", "localhost", "::1"])
        client_ip = request.client.host if request.client else "unknown"

        from src.core import admin
        if not admin.check_host_restriction(client_ip, allowed_hosts):
            logger.warning(f"Admin access denied from {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Access denied"}}
            )

    # Check origin if allowed_origins is configured
    allowed_origins = admin_config.get("allowed_origins", [])
    if allowed_origins:
        origin = request.headers.get("origin", "")
        if origin and origin not in allowed_origins:
            logger.warning(f"Admin access denied - origin {origin} not in allowed_origins")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Origin not allowed"}}
            )


def _get_admin_from_token(request: Request) -> int:
    """Get admin ID from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": 401, "message": "Invalid token"}}
        )

    token = auth_header[7:]
    
    # Allow secure self-test requests from users with admin permissions
    # Use scope directly as request.state can be unreliable with BaseHTTPMiddleware
    is_selftest = request.scope.get("state", {}).get("is_selftest", False)
    if not is_selftest:
        is_selftest = getattr(request.state, "is_selftest", False)

    # Direct header check as fallback
    if not is_selftest:
        internal_secret = api.get_internal_secret()
        provided_secret = request.headers.get("X-Plexichat-Internal-Secret")
        is_selftest = internal_secret and provided_secret == internal_secret

    user = request.scope.get("state", {}).get("user")
    if not user:
        user = getattr(request.state, "user", None)

    if is_selftest and user:
        from src.core.auth.permissions import has_permission
        if has_permission(user.permissions, "admin.*") or has_permission(user.permissions, "*"):
            # Return user_id but treat as admin
            return user.user_id

    from src.core import admin
    admin_id = admin.validate_session(token)

    if not admin_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": 401, "message": "Invalid or expired token"}}
        )

    return admin_id


# ==================== Auth Routes ====================

@router.post(
    "/login",
    response_model=AdminLoginResponse,
    summary="Admin login",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_login(
    request: Request,
    login_data: AdminLoginRequest
) -> AdminLoginResponse:
    """Admin login endpoint."""
    _check_host_restriction(request)

    from src.core import admin

    try:
        client_ip = request.client.host if request.client else "unknown"
        result = admin.login(login_data.username, login_data.password, client_ip)

        if not result.success:
            logger.warning(f"Admin login failed for user '{login_data.username}' from {client_ip}: {result.error}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": 401, "message": result.error}}
            )

        # If token is returned directly (OTP not required), login is complete
        if result.token:
            logger.info(f"Admin '{login_data.username}' logged in successfully")
            return AdminLoginResponse(status="success", token=result.token)

        if result.requires_otp_setup:
            logger.info(f"Admin '{login_data.username}' requires OTP setup")
            return AdminLoginResponse(
                status="otp_setup_required",
                admin_id=str(result.user_id),  # String to avoid JS precision loss
                otp_secret=result.otp_secret,
                otp_qr_uri=result.otp_qr_uri,
                message="Scan the QR code with your authenticator app, then enter the code"
            )

        if result.requires_otp_verify:
            logger.info(f"Admin '{login_data.username}' requires OTP verification")
            return AdminLoginResponse(
                status="otp_required",
                admin_id=str(result.user_id),  # String to avoid JS precision loss
                message="Enter your 2FA code"
            )

        logger.info(f"Admin '{login_data.username}' logged in successfully (default)")
        return AdminLoginResponse(status="success", token=result.token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in admin_login for '{login_data.username}': {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/verify-otp",
    response_model=AdminLoginResponse,
    summary="Verify admin OTP",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid admin_id"},
        401: {"model": ErrorResponse, "description": "Invalid or expired OTP code"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def verify_otp(
    request: Request,
    otp_data: OTPVerifyRequest
) -> AdminLoginResponse:
    """Verify OTP code for admin login."""
    _check_host_restriction(request)

    from src.core import admin

    # Convert string admin_id to int
    try:
        admin_id = int(otp_data.admin_id)
    except ValueError:
        logger.warning(f"Invalid admin_id format: {otp_data.admin_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid admin_id"}}
        )

    try:
        if otp_data.is_setup:
            result = admin.verify_otp_setup(admin_id, otp_data.code)
        else:
            result = admin.verify_otp(admin_id, otp_data.code)

        if not result.success:
            logger.warning(f"Admin OTP verification failed for admin {admin_id}: {result.error}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": 401, "message": result.error}}
            )

        logger.info(f"Admin {admin_id} OTP verified successfully")
        return AdminLoginResponse(status="success", token=result.token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in verify_otp for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/logout",
    response_model=SuccessResponse,
    summary="Admin logout",
    responses={
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_logout(request: Request) -> SuccessResponse:
    """Admin logout endpoint."""
    _check_host_restriction(request)

    try:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            from src.core import admin
            admin.logout(token)
            logger.info("Admin logged out successfully")

        return SuccessResponse(success=True)
    except Exception as e:
        logger.error(f"Admin logout failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Dashboard Routes ====================

@router.get(
    "/dashboard",
    response_model=AdminDashboardResponse,
    summary="Get admin dashboard data",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_dashboard(request: Request) -> AdminDashboardResponse:
    """Get admin dashboard data."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        ticket_counts = admin.get_ticket_counts()

        telemetry_stats = []
        try:
            from src.core import telemetry
            if telemetry.is_setup():
                stats = telemetry.get_endpoint_stats(hours=24)
                telemetry_stats = [
                    TelemetryEndpointStat(
                        endpoint=s.endpoint,
                        method=s.method,
                        count=s.count,
                        avg_ms=round(s.avg_response_time_ms, 2),
                        min_ms=round(getattr(s, "min_response_time_ms", 0), 2),
                        max_ms=round(getattr(s, "max_response_time_ms", 0), 2),
                        p50_ms=round(getattr(s, "p50_response_time_ms", 0), 2),
                        p95_ms=round(s.p95_response_time_ms, 2),
                        p99_ms=round(getattr(s, "p99_response_time_ms", 0), 2),
                        error_rate=round(s.error_rate * 100, 2)
                    )
                    for s in stats[:20]
                ]
        except Exception as te:
            logger.debug(f"Failed to get telemetry stats for dashboard: {te}")

        logger.info(f"Admin {admin_id} retrieved dashboard data")
        return AdminDashboardResponse(tickets=ticket_counts, telemetry=telemetry_stats)
    except Exception as e:
        logger.error(f"Failed to get dashboard data for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/tickets",
    response_model=List[TicketResponse],
    summary="Get feedback tickets",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_tickets(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[TicketResponse]:
    """Get feedback tickets."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin
    
    try:
        tickets = admin.get_feedback_tickets(status_filter, limit, offset)

        logger.debug(f"Admin {admin_id} retrieved {len(tickets)} tickets (status={status_filter})")
        return [
            TicketResponse(
                id=t.id, user_id=t.user_id, username=t.username,
                content=t.content, category=t.category, rating=t.rating,
                status=t.status, created_at=t.created_at,
                resolved_at=t.resolved_at, resolved_by=t.resolved_by
            )
            for t in tickets
        ]
    except Exception as e:
        logger.error(f"Failed to get tickets for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketResponse,
    summary="Get a single ticket",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Ticket not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_ticket(ticket_id: int, request: Request) -> TicketResponse:
    """Get a single ticket."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin
    
    try:
        ticket = admin.get_ticket(ticket_id)

        if not ticket:
            logger.warning(f"Ticket {ticket_id} not found (admin {admin_id})")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Ticket not found"}}
            )

        return TicketResponse(
            id=ticket.id, user_id=ticket.user_id, username=ticket.username,
            content=ticket.content, category=ticket.category, rating=ticket.rating,
            status=ticket.status, created_at=ticket.created_at,
            resolved_at=ticket.resolved_at, resolved_by=ticket.resolved_by
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get ticket {ticket_id} for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/tickets/{ticket_id}/status",
    response_model=SuccessResponse,
    summary="Update ticket status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Ticket not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_ticket_status(
    ticket_id: int,
    update: TicketStatusUpdate,
    request: Request
) -> SuccessResponse:
    """Update ticket status."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        if not admin.get_ticket(ticket_id):
            logger.warning(f"Ticket {ticket_id} not found for status update (admin {admin_id})")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Ticket not found"}}
            )

        admin.update_ticket_status(ticket_id, update.status, admin_id)
        logger.info(f"Admin {admin_id} updated ticket {ticket_id} status to {update.status}")

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update ticket {ticket_id} status by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/tickets/{ticket_id}/notes",
    response_model=List[NoteResponse],
    summary="Get internal notes for a ticket",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Ticket not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_ticket_notes(ticket_id: int, request: Request) -> List[NoteResponse]:
    """Get internal notes for a ticket."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        if not admin.get_ticket(ticket_id):
            logger.warning(f"Ticket {ticket_id} not found for notes retrieval (admin {admin_id})")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Ticket not found"}}
            )

        notes = admin.get_ticket_notes(ticket_id)

        return [
            NoteResponse(
                id=n.id, ticket_id=n.ticket_id, admin_id=n.admin_id,
                admin_username=n.admin_username, content=n.content, created_at=n.created_at
            )
            for n in notes
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get ticket {ticket_id} notes for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/tickets/{ticket_id}/notes",
    response_model=NoteResponse,
    summary="Add internal note to a ticket",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Ticket not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_ticket_note(
    ticket_id: int,
    note: InternalNoteCreate,
    request: Request
) -> NoteResponse:
    """Add an internal note to a ticket."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        if not admin.get_ticket(ticket_id):
            logger.warning(f"Ticket {ticket_id} not found for note addition (admin {admin_id})")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Ticket not found"}}
            )

        new_note = admin.add_internal_note(ticket_id, admin_id, note.content)
        
        if new_note is None:
            logger.error(f"Failed to create note for ticket {ticket_id} by admin {admin_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to create note"}}
            )

        logger.info(f"Admin {admin_id} added note to ticket {ticket_id}")
        return NoteResponse(
            id=new_note.id, ticket_id=new_note.ticket_id, admin_id=new_note.admin_id,
            admin_username=new_note.admin_username, content=new_note.content,
            created_at=new_note.created_at
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add note to ticket {ticket_id} by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/telemetry/stats",
    response_model=TelemetryStatsResponse,
    summary="Get telemetry statistics",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_telemetry_stats(
    request: Request,
    hours: int = 24,
    endpoint: Optional[str] = None,
    source: Optional[str] = None
) -> TelemetryStatsResponse:
    """
    Get telemetry statistics.
    
    Args:
        hours: Number of hours to look back (default 24)
        endpoint: Optional endpoint pattern to filter by
        source: Optional source filter - "server" for server-side only, "client" for client-side only
    """
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            logger.info(f"Admin {admin_id} requested telemetry stats but telemetry is not setup")
            return TelemetryStatsResponse(stats=[], source=source or "all")

        # Map source to client_id filter
        client_id_filter = None
        if source == "server":
            client_id_filter = "server"
        elif source == "client":
            # For client, we want everything except server
            pass

        stats = telemetry.get_endpoint_stats(
            hours=hours,
            endpoint_filter=endpoint,
            client_id_filter=client_id_filter
        )

        # Normalize emoji endpoints for display
        import urllib.parse

        logger.debug(f"Admin {admin_id} retrieved {len(stats)} telemetry stats")
        return TelemetryStatsResponse(
            stats=[
                TelemetryEndpointStat(
                    endpoint=urllib.parse.unquote(s.endpoint),
                    method=s.method,
                    count=s.count,
                    avg_ms=round(s.avg_response_time_ms, 2),
                    min_ms=round(getattr(s, "min_response_time_ms", 0), 2),
                    max_ms=round(getattr(s, "max_response_time_ms", 0), 2),
                    p50_ms=round(getattr(s, "p50_response_time_ms", 0), 2),
                    p95_ms=round(s.p95_response_time_ms, 2),
                    p99_ms=round(getattr(s, "p99_response_time_ms", 0), 2),
                    error_rate=round(s.error_rate * 100, 2)
                )
                for s in stats
            ],
            source=source or "all"
        )
    except Exception as e:
        logger.error(f"Failed to get telemetry stats for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/telemetry/history",
    response_model=TelemetryHistoryResponse,
    summary="Get telemetry history for an endpoint",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_telemetry_history(
    request: Request,
    endpoint: str,
    method: str = "GET",
    hours: int = 24,
    bucket_minutes: int = 5
) -> TelemetryHistoryResponse:
    """Get telemetry history for an endpoint."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            logger.info(f"Admin {admin_id} requested telemetry history but telemetry is not setup")
            return TelemetryHistoryResponse(history=[])

        history = telemetry.get_response_time_history(
            endpoint=endpoint,
            method=method.upper(),
            hours=hours,
            bucket_minutes=bucket_minutes
        )

        logger.debug(f"Admin {admin_id} retrieved telemetry history for {method} {endpoint}")
        return TelemetryHistoryResponse(history=history)
    except Exception as e:
        logger.error(f"Failed to get telemetry history for {method} {endpoint} (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.post(
    "/telemetry/reset",
    response_model=TelemetryResetResponse,
    summary="Reset all telemetry statistics",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def reset_telemetry_stats(request: Request) -> TelemetryResetResponse:
    """Reset all telemetry statistics."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            logger.info(f"Admin {admin_id} requested telemetry reset but telemetry is not setup")
            return TelemetryResetResponse(success=False, deleted_count=0)

        deleted_count = telemetry.reset_all_stats()
        logger.info(f"Admin {admin_id} reset telemetry stats, deleted {deleted_count} records")

        return TelemetryResetResponse(success=True, deleted_count=deleted_count)
    except Exception as e:
        logger.error(f"Failed to reset telemetry stats by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/telemetry/export",
    response_model=TelemetryExportResponse,
    summary="Export telemetry statistics",
    responses={
        200: {
            "description": "Exported statistics in the requested format",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/TelemetryExportResponse"}},
                "text/csv": {"schema": {"type": "string"}},
                "text/plain": {"schema": {"type": "string"}},
                "text/html": {"schema": {"type": "string"}},
            }
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def export_telemetry_stats(
    request: Request,
    format: str = "json",
    hours: int = 24
) -> Union[TelemetryExportResponse, Response]:
    """
    Export telemetry statistics in various formats.
    
    Args:
        format: Export format - "json", "html", "txt", or "csv"
        hours: Number of hours to include
    """
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            logger.error(f"Admin {admin_id} requested telemetry export but telemetry is not setup")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Telemetry not initialized"}}
            )

        stats = telemetry.get_endpoint_stats(hours=hours)
        export_time = time.strftime('%Y-%m-%d %H:%M:%S')

        logger.info(f"Admin {admin_id} exporting telemetry stats (format={format}, hours={hours})")

        if format == "json":
            return TelemetryExportResponse(
                export_time=export_time,
                hours=hours,
                stats=[
                    TelemetryEndpointStat(
                        endpoint=s.endpoint,
                        method=s.method,
                        count=s.count,
                        avg_ms=round(s.avg_response_time_ms, 2),
                        min_ms=round(s.min_response_time_ms, 2),
                        max_ms=round(s.max_response_time_ms, 2),
                        p50_ms=round(s.p50_response_time_ms, 2),
                        p95_ms=round(s.p95_response_time_ms, 2),
                        p99_ms=round(s.p99_response_time_ms, 2),
                        error_rate=round(s.error_rate * 100, 2)
                    )
                    for s in stats
                ]
            )

        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Endpoint", "Method", "Count", "Avg (ms)", "Min (ms)", "Max (ms)", "P50 (ms)", "P95 (ms)", "P99 (ms)", "Error %"])
            for s in stats:
                writer.writerow([
                    s.endpoint, s.method, s.count,
                    round(s.avg_response_time_ms, 2),
                    round(s.min_response_time_ms, 2),
                    round(s.max_response_time_ms, 2),
                    round(s.p50_response_time_ms, 2),
                    round(s.p95_response_time_ms, 2),
                    round(s.p99_response_time_ms, 2),
                    round(s.error_rate * 100, 2)
                ])
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.csv"}
            )

        elif format == "txt":
            lines = [
                "PlexiChat Telemetry Report",
                f"Generated: {export_time}",
                f"Time Range: Last {hours} hours",
                "",
                f"{'Endpoint':<50} {'Method':<8} {'Count':>8} {'Avg':>10} {'P95':>10} {'Error%':>8}",
                f"{'-'*50} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*8}",
            ]
            for s in stats:
                lines.append(
                    f"{s.endpoint[:50]:<50} {s.method:<8} {s.count:>8} {s.avg_response_time_ms:>9.1f}ms {s.p95_response_time_ms:>9.1f}ms {s.error_rate*100:>7.1f}%"
                )

            # Summary
            if stats:
                total_requests = sum(s.count for s in stats)
                avg_latency = sum(s.avg_response_time_ms * s.count for s in stats) / total_requests if total_requests else 0
                avg_error = sum(s.error_rate * s.count for s in stats) / total_requests * 100 if total_requests else 0
                lines.extend([
                    "",
                    "Summary:",
                    f"  Total Requests: {total_requests:,}",
                    f"  Average Latency: {avg_latency:.1f}ms",
                    f"  Average Error Rate: {avg_error:.1f}%",
                    f"  Endpoints Tracked: {len(stats)}",
                ])

            return Response(
                content="\n".join(lines),
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.txt"}
            )

        elif format == "html":
            # Generate HTML report
            total_requests = sum(s.count for s in stats) if stats else 0
            avg_latency = sum(s.avg_response_time_ms * s.count for s in stats) / total_requests if total_requests else 0
            avg_error = sum(s.error_rate * s.count for s in stats) / total_requests * 100 if total_requests else 0

            rows_html = ""
            for s in stats:
                latency_class = "good" if s.avg_response_time_ms < 100 else "warn" if s.avg_response_time_ms < 500 else "bad"
                error_class = "good" if s.error_rate < 0.01 else "warn" if s.error_rate < 0.05 else "bad"
                rows_html += f"""
                <tr>
                    <td>{s.endpoint}</td>
                    <td>{s.method}</td>
                    <td>{s.count:,}</td>
                    <td class="{latency_class}">{s.avg_response_time_ms:.1f}</td>
                    <td>{s.min_response_time_ms:.1f}</td>
                    <td>{s.max_response_time_ms:.1f}</td>
                    <td>{s.p50_response_time_ms:.1f}</td>
                    <td class="{latency_class}">{s.p95_response_time_ms:.1f}</td>
                    <td>{s.p99_response_time_ms:.1f}</td>
                    <td class="{error_class}">{s.error_rate*100:.1f}%</td>
                </tr>"""

            html = f"""<!DOCTYPE html>
<html>
<head>
    <title>PlexiChat Telemetry Report - {export_time}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eaeaea; padding: 20px; }}
        h1 {{ color: #e94560; }}
        .summary {{ display: flex; gap: 20px; margin: 20px 0; flex-wrap: wrap; }}
        .stat {{ background: #16213e; padding: 15px 25px; border-radius: 8px; border: 1px solid #0f3460; }}
        .stat h3 {{ font-size: 12px; color: #888; margin: 0 0 5px 0; text-transform: uppercase; }}
        .stat .value {{ font-size: 24px; font-weight: bold; color: #e94560; }}
        table {{ width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; margin-top: 20px; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #0f3460; }}
        th {{ background: #0f3460; font-size: 12px; text-transform: uppercase; }}
        .good {{ color: #4ade80; }}
        .warn {{ color: #fbbf24; }}
        .bad {{ color: #ef4444; }}
        @media print {{ body {{ background: white; color: black; }} table {{ background: white; }} th {{ background: #eee; }} }}
    </style>
</head>
<body>
    <h1>PlexiChat Telemetry Report</h1>
    <p>Generated: {export_time} | Time Range: Last {hours} hours</p>
    <div class="summary">
        <div class="stat"><h3>Total Requests</h3><div class="value">{total_requests:,}</div></div>
        <div class="stat"><h3>Avg Latency</h3><div class="value">{avg_latency:.0f}ms</div></div>
        <div class="stat"><h3>Avg Error Rate</h3><div class="value">{avg_error:.1f}%</div></div>
        <div class="stat"><h3>Endpoints</h3><div class="value">{len(stats)}</div></div>
    </div>
    <table>
        <thead>
            <tr><th>Endpoint</th><th>Method</th><th>Count</th><th>Avg (ms)</th><th>Min</th><th>Max</th><th>P50</th><th>P95</th><th>P99</th><th>Error %</th></tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
</body>
</html>"""
            return Response(
                content=html,
                media_type="text/html",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.html"}
            )

        else:
            logger.warning(f"Admin {admin_id} requested unsupported export format: {format}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": f"Unsupported format: {format}. Use json, csv, txt, or html"}}
            )

    except HTTPException:
        raise
    except ImportError:
        logger.error(f"Telemetry module not available for admin {admin_id}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )
    except Exception as e:
        logger.error(f"Failed to export telemetry stats for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


# ==================== Hash Reports Routes ====================

@router.get(
    "/hash-reports",
    response_model=List[HashReportResponse],
    summary="Get hash reports",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_hash_reports(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[HashReportResponse]:
    """Get hash reports for admin review with image preview support."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin
    try:
        reports = admin.get_hash_reports(status_filter, limit, offset)
        logger.debug(f"Admin {admin_id} retrieved {len(reports)} hash reports (filter={status_filter})")

        return [
            HashReportResponse(
                id=r.id,
                hash_value=r.hash_value,
                phash_value=r.phash_value,
                reporter_id=r.reporter_id,
                reporter_username=r.reporter_username,
                reason=r.reason,
                details=r.details,
                status=r.status,
                reported_at=r.reported_at,
                reviewed_at=r.reviewed_at,
                reviewed_by=r.reviewed_by,
                admin_notes=r.admin_notes,
                uploader_id=r.uploader_id,
                message_id=r.message_id,
                attachment_url=r.attachment_url,
                block_uploader=r.block_uploader
            )
            for r in reports
        ]
    except Exception as e:
        logger.error(f"Failed to get hash reports for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/hash-reports/counts",
    response_model=HashReportCountsResponse,
    summary="Get hash report counts",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_hash_report_counts(request: Request) -> HashReportCountsResponse:
    """Get counts of hash reports by status."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin
    try:
        counts = admin.get_hash_report_counts()
        logger.debug(f"Admin {admin_id} retrieved hash report counts")
        return HashReportCountsResponse(**counts)
    except Exception as e:
        logger.error(f"Failed to get hash report counts for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.post(
    "/hash-reports/{report_id}/review",
    response_model=HashReportReviewResponse,
    summary="Review a hash report",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Report not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def review_hash_report(
    report_id: int,
    review: HashReportReviewRequest,
    request: Request
) -> HashReportReviewResponse:
    """Review a hash report (block, clear, or dismiss)."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        success = admin.review_hash_report(report_id, admin_id, review.action, review.notes)

        if not success:
            logger.warning(f"Hash report {report_id} not found for review by admin {admin_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Report not found"}}
            )

        logger.info(f"Admin {admin_id} reviewed hash report {report_id}: {review.action}")

        return HashReportReviewResponse(success=True, action=review.action)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to review hash report {report_id} by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/blocked-hashes",
    response_model=List[BlockedHashResponse],
    summary="Get blocked hashes",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_blocked_hashes(
    request: Request,
    limit: int = 100,
    offset: int = 0
) -> List[BlockedHashResponse]:
    """Get list of blocked hashes."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin
    try:
        hashes = admin.get_blocked_hashes(limit, offset)
        logger.debug(f"Admin {admin_id} retrieved {len(hashes)} blocked hashes")

        return [
            BlockedHashResponse(
                hash_value=h.hash_value,
                reason=h.reason,
                blocked_at=h.blocked_at,
                blocked_by=h.blocked_by,
                auto_blocked=h.auto_blocked,
                hash_type=h.hash_type,
                phash_threshold=h.phash_threshold
            )
            for h in hashes
        ]
    except Exception as e:
        logger.error(f"Failed to get blocked hashes for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.post(
    "/blocked-hashes",
    response_model=BlockHashResponse,
    summary="Manually block a hash",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_hash_manually(
    block_request: ManualBlockHashRequest,
    request: Request
) -> BlockHashResponse:
    """Manually block a hash (SHA-256 or pHash)."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        # Determine hash type based on length (pHash is typically 16 chars, SHA-256 is 64)
        hash_type = "phash" if len(block_request.hash_value) <= 32 else "sha256"
        phash_threshold = 10 if hash_type == "phash" else 0

        success = admin.block_hash(
            block_request.hash_value,
            block_request.reason,
            admin_id,
            hash_type=hash_type,
            phash_threshold=phash_threshold
        )

        if not success:
            logger.error(f"Failed to manually block {hash_type} hash by admin {admin_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to block hash"}}
            )

        logger.info(f"Admin {admin_id} manually blocked {hash_type} hash {block_request.hash_value[:16]}...")

        return BlockHashResponse(success=True, hash_value=block_request.hash_value, hash_type=hash_type)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to manually block hash by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.delete(
    "/blocked-hashes/{hash_value}",
    response_model=SuccessResponse,
    summary="Unblock a hash",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unblock_hash(
    hash_value: str,
    request: Request
) -> SuccessResponse:
    """Unblock a hash."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        success = admin.unblock_hash(hash_value)

        if not success:
            logger.warning(f"Failed to unblock hash {hash_value[:16]}... by admin {admin_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to unblock hash"}}
            )

        logger.info(f"Admin {admin_id} unblocked hash {hash_value[:16]}...")
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to unblock hash {hash_value[:16]}... by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


# ==================== Blocked Users (Media Uploads) ====================

@router.get(
    "/blocked-users",
    response_model=List[BlockedUserResponse],
    summary="Get blocked users",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_blocked_users(
    request: Request,
    limit: int = 100,
    offset: int = 0
) -> List[BlockedUserResponse]:
    """Get list of users blocked from uploading media."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin
    try:
        users = admin.get_blocked_users(limit, offset)
        logger.debug(f"Admin {admin_id} retrieved {len(users)} blocked users")

        return [
            BlockedUserResponse(
                user_id=u.user_id,
                username=u.username,
                reason=u.reason,
                blocked_at=u.blocked_at,
                blocked_by=u.blocked_by,
                expires_at=u.expires_at
            )
            for u in users
        ]
    except Exception as e:
        logger.error(f"Failed to get blocked users for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.post(
    "/blocked-users",
    response_model=BlockUserResponse,
    summary="Block a user from uploading",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_user(
    block_request: BlockUserRequest,
    request: Request
) -> BlockUserResponse:
    """Block a user from uploading media."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        success = admin.block_user(
            block_request.user_id,
            block_request.reason,
            admin_id,
            block_request.duration_hours
        )

        if not success:
            logger.error(f"Failed to block user {block_request.user_id} by admin {admin_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to block user"}}
            )

        logger.info(f"Admin {admin_id} blocked user {block_request.user_id} from uploads")

        return BlockUserResponse(success=True, user_id=block_request.user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to block user {block_request.user_id} by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.delete(
    "/blocked-users/{user_id}",
    response_model=SuccessResponse,
    summary="Unblock a user from uploading",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unblock_user(
    user_id: int,
    request: Request
) -> SuccessResponse:
    """Unblock a user from uploading media."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        success = admin.unblock_user(user_id)

        if not success:
            logger.warning(f"Failed to unblock user {user_id} by admin {admin_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to unblock user"}}
            )

        logger.info(f"Admin {admin_id} unblocked user {user_id} from uploads")

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to unblock user {user_id} by admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


# ==================== User Tier Management ====================

@router.get(
    "/users/search",
    response_model=UserSearchListResponse,
    summary="Search users",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_user_search(
    q: str,
    request: Request,
    limit: int = 20,
    offset: int = 0
) -> UserSearchListResponse:
    """Search users by username or ID."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    if not q or len(q) < 2:
        return UserSearchListResponse(users=[])

    try:
        admin_core = api.get_admin()
        if not admin_core:
            logger.error(f"Admin module not available for user search (admin {admin_id})")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}}
            )

        users_data = admin_core.search_users(q, limit, offset)
        logger.debug(f"Admin {admin_id} searched users with query '{q}', found {len(users_data)} results")
        
        users = []
        for user in users_data:
            users.append(UserSearchResponse(
                id=str(user.id),
                username=user.username,
                email=user.email,
                tier=user.tier,
                badges=user.badges,
                created_at=user.created_at
            ))

        return UserSearchListResponse(users=users)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search users with query '{q}' (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/users/{user_id}",
    response_model=UserDetailsResponse,
    summary="Get user details",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "User not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_details(
    user_id: int,
    request: Request
) -> UserDetailsResponse:
    """Get user details by ID."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        admin_core = api.get_admin()
        if not admin_core:
            logger.error(f"Admin module not available for user details (admin {admin_id})")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}}
            )

        user = admin_core.get_user_details(user_id)
        if not user:
            logger.warning(f"Admin {admin_id} requested details for non-existent user {user_id}")
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        logger.debug(f"Admin {admin_id} retrieved details for user {user_id}")

        return UserDetailsResponse(
            id=str(user.id),
            username=user.username,
            email=user.email,
            tier=user.tier,
            badges=user.badges,
            created_at=user.created_at,
            last_login=user.last_login
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get user details for {user_id} (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.put(
    "/users/{user_id}/tier",
    response_model=UserTierUpdateResponse,
    summary="Update user tier",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "User not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_user_tier(
    user_id: str,
    update: UserTierUpdate,
    request: Request
) -> UserTierUpdateResponse:
    """Update a user's tier."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        try:
            uid = int(user_id)
        except ValueError:
            logger.warning(f"Admin {admin_id} provided invalid user ID for tier update: {user_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        admin_core = api.get_admin()
        if not admin_core:
            logger.error(f"Admin module not available for tier update (admin {admin_id})")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}}
            )

        success = admin_core.update_user_tier(uid, update.tier)
        if not success:
            logger.warning(f"Admin {admin_id} failed to update tier for non-existent user {uid}")
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        logger.info(f"Admin {admin_id} updated user {uid} tier to {update.tier}")

        return UserTierUpdateResponse(success=True, user_id=user_id, tier=update.tier)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update user {user_id} tier (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.post(
    "/users/{user_id}/badges/{badge}",
    response_model=UserBadgeUpdateResponse,
    summary="Add user badge",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "User not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_user_badge(
    user_id: str,
    badge: str,
    request: Request
) -> UserBadgeUpdateResponse:
    """Add a badge to a user."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        try:
            uid = int(user_id)
        except ValueError:
            logger.warning(f"Admin {admin_id} provided invalid user ID for badge add: {user_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        admin_core = api.get_admin()
        if not admin_core:
            logger.error(f"Admin module not available for badge add (admin {admin_id})")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}}
            )

        badges = admin_core.add_user_badge(uid, badge)
        if badges is None:
            logger.warning(f"Admin {admin_id} failed to add badge '{badge}' to non-existent user {uid}")
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        logger.info(f"Admin {admin_id} added badge '{badge}' to user {uid}")

        return UserBadgeUpdateResponse(success=True, badges=badges)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add badge '{badge}' to user {user_id} (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.delete(
    "/users/{user_id}/badges/{badge}",
    response_model=UserBadgeUpdateResponse,
    summary="Remove user badge",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "User not found or Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_user_badge(
    user_id: str,
    badge: str,
    request: Request
) -> UserBadgeUpdateResponse:
    """Remove a badge from a user."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        try:
            uid = int(user_id)
        except ValueError:
            logger.warning(f"Admin {admin_id} provided invalid user ID for badge removal: {user_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        admin_core = api.get_admin()
        if not admin_core:
            logger.error(f"Admin module not available for badge removal (admin {admin_id})")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}}
            )

        badges = admin_core.remove_user_badge(uid, badge)
        if badges is None:
            logger.warning(f"Admin {admin_id} failed to remove badge '{badge}' from non-existent user {uid}")
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        logger.info(f"Admin {admin_id} removed badge '{badge}' from user {uid}")

        return UserBadgeUpdateResponse(success=True, badges=badges)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove badge '{badge}' from user {user_id} (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/tiers",
    response_model=AvailableTiersResponse,
    summary="Get available tiers",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_available_tiers(request: Request) -> AvailableTiersResponse:
    """Get list of available tiers."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        logger.debug(f"Admin {admin_id} requested available tiers")
        return AvailableTiersResponse(
            tiers=[
                AvailableTierInfo(id="free", name="Free", description="Basic access"),
                AvailableTierInfo(id="alpha", name="Alpha", description="Alpha tester access"),
                AvailableTierInfo(id="beta", name="Beta", description="Beta tester access"),
                AvailableTierInfo(id="premium", name="Premium", description="Premium features"),
                AvailableTierInfo(id="staff", name="Staff", description="Staff member")
            ]
        )
    except Exception as e:
        logger.error(f"Failed to get available tiers (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/badges",
    response_model=AvailableBadgesResponse,
    summary="Get available badges",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_available_badges(request: Request) -> AvailableBadgesResponse:
    """Get list of available badges."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        badges_config = config.get("user_features", {}).get("available_badges", [])
        
        badges = badges_config or [
            "alpha_tester", "early_supporter", "staff",
            "verified", "bug_hunter", "contributor", "moderator", "partner"
        ]

        logger.debug(f"Admin {admin_id} requested available badges, found {len(badges)}")
        return AvailableBadgesResponse(badges=badges)
    except Exception as e:
        logger.error(f"Failed to get available badges (admin {admin_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}}
        )


# ==================== Admin UI HTML ====================

ADMIN_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlexiChat Admin Login</title>
    <style>
        :root { --bg: #1a1a2e; --card: #16213e; --accent: #e94560; --text: #eaeaea; --border: #0f3460; --error: #ef4444; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-card { background: var(--card); border-radius: 12px; padding: 40px; width: 100%; max-width: 400px; border: 1px solid var(--border); }
        h1 { color: var(--accent); margin-bottom: 8px; font-size: 24px; }
        .subtitle { color: #888; margin-bottom: 24px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; margin-bottom: 6px; font-size: 14px; color: #aaa; }
        input { width: 100%; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); font-size: 16px; }
        input:focus { outline: none; border-color: var(--accent); }
        button { width: 100%; padding: 12px; background: var(--accent); color: white; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; margin-top: 8px; }
        button:hover { opacity: 0.9; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .error { color: var(--error); font-size: 14px; margin-top: 12px; display: none; }
        .otp-setup { display: none; text-align: center; }
        .otp-setup img { max-width: 200px; margin: 16px 0; background: white; padding: 8px; border-radius: 8px; }
        .otp-setup .secret { font-family: monospace; background: var(--bg); padding: 8px; border-radius: 4px; margin: 8px 0; word-break: break-all; }
        .warning { background: #fbbf24; color: #000; padding: 12px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="login-card">
        <div id="login-form">
            <h1>PlexiChat Admin</h1>
            <p class="subtitle">Sign in to access the admin panel</p>
            <form onsubmit="login(event)">
                <div class="form-group">
                    <label for="username">Username</label>
                    <input type="text" id="username" required autocomplete="username">
                </div>
                <div class="form-group">
                    <label for="password">Password</label>
                    <input type="password" id="password" required autocomplete="current-password">
                </div>
                <button type="submit" id="login-btn">Sign In</button>
            </form>
            <div class="error" id="login-error"></div>
        </div>
        <div id="otp-setup" class="otp-setup">
            <h1>Setup 2FA</h1>
            <p class="subtitle">Scan this QR code with your authenticator app</p>
            <div class="warning">2FA is required for admin access. This is a one-time setup.</div>
            <img id="qr-code" src="" alt="QR Code">
            <p>Or enter this secret manually:</p>
            <div class="secret" id="otp-secret"></div>
            <form onsubmit="verifyOTP(event, true)">
                <div class="form-group">
                    <label for="setup-code">Enter code from app</label>
                    <input type="text" id="setup-code" required maxlength="6" pattern="[0-9]{6}" autocomplete="one-time-code">
                </div>
                <button type="submit">Verify & Enable 2FA</button>
            </form>
            <div class="error" id="setup-error"></div>
        </div>
        <div id="otp-verify" class="otp-setup">
            <h1>Enter 2FA Code</h1>
            <p class="subtitle">Enter the code from your authenticator app</p>
            <form onsubmit="verifyOTP(event, false)">
                <div class="form-group">
                    <label for="verify-code">6-digit code</label>
                    <input type="text" id="verify-code" required maxlength="6" pattern="[0-9]{6}" autocomplete="one-time-code">
                </div>
                <button type="submit">Verify</button>
            </form>
            <div class="error" id="verify-error"></div>
        </div>
    </div>
    <script>
        let adminId = null;
        async function login(e) {
            e.preventDefault();
            const btn = document.getElementById('login-btn');
            const err = document.getElementById('login-error');
            btn.disabled = true; err.style.display = 'none';
            try {
                const res = await fetch('/api/v1/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: document.getElementById('username').value,
                        password: document.getElementById('password').value
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Login failed');
                if (data.status === 'otp_setup_required') {
                    adminId = data.admin_id;
                    document.getElementById('login-form').style.display = 'none';
                    document.getElementById('otp-setup').style.display = 'block';
                    document.getElementById('qr-code').src = '/api/v1/qr?size=200x200&data=' + encodeURIComponent(data.otp_qr_uri);
                    document.getElementById('otp-secret').textContent = data.otp_secret;
                } else if (data.status === 'otp_required') {
                    adminId = data.admin_id;
                    document.getElementById('login-form').style.display = 'none';
                    document.getElementById('otp-verify').style.display = 'block';
                } else if (data.token) {
                    localStorage.setItem('plexichat-admin-token', data.token);
                    window.location.href = '/api/v1/admin/ui';
                }
            } catch (e) { err.textContent = e.message; err.style.display = 'block'; }
            btn.disabled = false;
        }
        async function verifyOTP(e, isSetup) {
            e.preventDefault();
            const code = document.getElementById(isSetup ? 'setup-code' : 'verify-code').value;
            const err = document.getElementById(isSetup ? 'setup-error' : 'verify-error');
            err.style.display = 'none';
            try {
                const res = await fetch('/api/v1/admin/verify-otp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ admin_id: adminId, code: code, is_setup: isSetup })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Verification failed');
                localStorage.setItem('plexichat-admin-token', data.token);
                window.location.href = '/api/v1/admin/ui';
            } catch (e) { err.textContent = e.message; err.style.display = 'block'; }
        }
        // Check if already logged in
        const token = localStorage.getItem('plexichat-admin-token');
        if (token) {
            fetch('/api/v1/admin/dashboard', { headers: { 'Authorization': 'Bearer ' + token } })
                .then(r => { if (r.ok) window.location.href = '/api/v1/admin/ui'; });
        }
    </script>
</body>
</html>
"""


ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PlexiChat Admin Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root { --bg: #1a1a2e; --card: #16213e; --accent: #e94560; --text: #eaeaea; --border: #0f3460; --good: #4ade80; --warn: #fbbf24; --bad: #ef4444; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
        .header { background: var(--card); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); }
        .header h1 { color: var(--accent); font-size: 20px; }
        .logout-btn { background: transparent; border: 1px solid var(--border); color: var(--text); padding: 8px 16px; border-radius: 4px; cursor: pointer; }
        .logout-btn:hover { border-color: var(--accent); }
        .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--card); border-radius: 8px; padding: 20px; border: 1px solid var(--border); }
        .card h3 { font-size: 12px; color: #888; margin-bottom: 8px; text-transform: uppercase; }
        .card .value { font-size: 28px; font-weight: bold; color: var(--accent); }
        .card .value.good { color: var(--good); }
        .card .value.warn { color: var(--warn); }
        .card .value.bad { color: var(--bad); }
        .card .subtitle { font-size: 11px; color: #666; margin-top: 4px; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
        .tab { padding: 10px 20px; background: var(--card); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; color: var(--text); }
        .tab.active { background: var(--accent); border-color: var(--accent); }
        table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 8px; overflow: hidden; }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); }
        th { background: var(--border); font-weight: 600; font-size: 12px; text-transform: uppercase; }
        .status { padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
        .status.open { background: #fbbf24; color: #000; }
        .status.in_progress { background: #3b82f6; color: #fff; }
        .status.resolved { background: #4ade80; color: #000; }
        .status.closed { background: #6b7280; color: #fff; }
        #error { color: #ef4444; padding: 16px; background: rgba(239,68,68,0.1); border-radius: 8px; margin-bottom: 16px; display: none; }
        .loading { text-align: center; padding: 40px; color: #888; }
        .chart-container { background: var(--card); border-radius: 8px; padding: 20px; border: 1px solid var(--border); margin-bottom: 16px; }
        .chart-container h3 { font-size: 14px; color: #888; margin-bottom: 16px; text-transform: uppercase; }
        .chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
        .chart-row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
        @media (max-width: 1200px) { .chart-row-3 { grid-template-columns: 1fr 1fr; } }
        @media (max-width: 900px) { .chart-row, .chart-row-3 { grid-template-columns: 1fr; } }
        .latency-good { color: var(--good); }
        .latency-warn { color: var(--warn); }
        .latency-bad { color: var(--bad); }
        .endpoint-row { cursor: pointer; transition: background 0.2s; }
        .endpoint-row:hover { background: rgba(233,69,96,0.1); }
        .endpoint-row.selected { background: rgba(233,69,96,0.2); }
        .emoji-cell { font-family: "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif; }
        #history-chart-container { display: none; }
        .summary-row { display: flex; gap: 24px; margin-bottom: 16px; flex-wrap: wrap; }
        .summary-item { display: flex; align-items: center; gap: 8px; font-size: 14px; }
        .summary-dot { width: 12px; height: 12px; border-radius: 50%; }
        .filter-row { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; }
        .filter-row select, .filter-row input { padding: 8px 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; color: var(--text); }
        .filter-row select:focus, .filter-row input:focus { outline: none; border-color: var(--accent); }
        .refresh-btn { padding: 8px 16px; background: var(--accent); border: none; border-radius: 4px; color: white; cursor: pointer; }
        .refresh-btn:hover { opacity: 0.9; }
    </style>
</head>
<body>
    <div class="header">
        <h1>PlexiChat Admin</h1>
        <button class="logout-btn" onclick="logout()">Logout</button>
    </div>
    <div class="container">
        <div id="error"></div>
        <div id="loading" class="loading">Verifying session...</div>
        <div id="content" style="display:none">
            <div class="cards" id="stats"></div>
            <div class="tabs">
                <button class="tab active" onclick="showTab('tickets', this)">Tickets</button>
                <button class="tab" onclick="showTab('telemetry', this)">Telemetry</button>
                <button class="tab" onclick="showTab('hash-reports', this)">Hash Reports</button>
                <button class="tab" onclick="showTab('user-tiers', this)">User Tiers</button>
            </div>
            <div id="tickets-tab">
                <table id="tickets-table">
                    <thead><tr><th>ID</th><th>User</th><th>Category</th><th>Status</th><th>Created</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="hash-reports-tab" style="display:none">
                <div class="filter-row">
                    <select id="hash-status-filter" onchange="loadHashReports()">
                        <option value="">All Status</option>
                        <option value="pending">Pending</option>
                        <option value="reviewed">Reviewed</option>
                        <option value="blocked">Blocked</option>
                        <option value="cleared">Cleared</option>
                    </select>
                    <button class="refresh-btn" onclick="loadHashReports()">Refresh</button>
                    <button class="refresh-btn" onclick="showBlockHashModal()" style="background:#ef4444;margin-left:auto">Block Hash</button>
                </div>
                <div class="cards" id="hash-report-stats"></div>
                <h3 style="margin:16px 0 8px;color:#888;font-size:14px">PENDING REPORTS</h3>
                <table id="hash-reports-table">
                    <thead><tr><th>Hash</th><th>Reporter</th><th>Reason</th><th>Status</th><th>Reported</th><th>Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
                <h3 style="margin:24px 0 8px;color:#888;font-size:14px">BLOCKED HASHES</h3>
                <table id="blocked-hashes-table">
                    <thead><tr><th>Hash</th><th>Reason</th><th>Blocked At</th><th>Auto</th><th>Actions</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <!-- Block Hash Modal -->
            <div id="block-hash-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center">
                <div style="background:var(--card);border-radius:12px;padding:24px;width:100%;max-width:500px;border:1px solid var(--border)">
                    <h2 style="color:var(--accent);margin-bottom:16px">Block Hash</h2>
                    <div class="form-group" style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:#aaa">SHA-256 Hash</label>
                        <input type="text" id="block-hash-value" style="width:100%;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:monospace" placeholder="Enter 64-character hash">
                    </div>
                    <div class="form-group" style="margin-bottom:16px">
                        <label style="display:block;margin-bottom:6px;color:#aaa">Reason</label>
                        <input type="text" id="block-hash-reason" style="width:100%;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)" placeholder="Reason for blocking">
                    </div>
                    <div style="display:flex;gap:12px;justify-content:flex-end">
                        <button onclick="hideBlockHashModal()" style="padding:10px 20px;background:transparent;border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer">Cancel</button>
                        <button onclick="blockHashManually()" style="padding:10px 20px;background:var(--accent);border:none;border-radius:4px;color:white;cursor:pointer">Block Hash</button>
                    </div>
                </div>
            </div>
            <div id="telemetry-tab" style="display:none">
                <div class="filter-row">
                    <select id="time-range" onchange="loadTelemetryStats()">
                        <option value="1">Last 1 hour</option>
                        <option value="6">Last 6 hours</option>
                        <option value="24" selected>Last 24 hours</option>
                        <option value="72">Last 3 days</option>
                        <option value="168">Last 7 days</option>
                    </select>
                    <select id="telemetry-source" onchange="loadTelemetryStats()">
                        <option value="">All Sources</option>
                        <option value="server">Server-side Only</option>
                        <option value="client">Client-side Only</option>
                    </select>
                    <button class="refresh-btn" onclick="loadTelemetryStats()">Refresh</button>
                    <select id="export-format" style="margin-left:auto">
                        <option value="json">JSON</option>
                        <option value="csv">CSV</option>
                        <option value="html">HTML</option>
                        <option value="txt">Text</option>
                    </select>
                    <button class="refresh-btn" onclick="exportStats()" style="background:#3b82f6">Export</button>
                    <button class="refresh-btn" onclick="resetStats()" style="background:#ef4444">Reset All</button>
                </div>
                <div class="cards" id="telemetry-summary"></div>
                <div class="chart-row">
                    <div class="chart-container">
                        <h3>Latency Distribution (Avg vs P95)</h3>
                        <canvas id="latency-chart"></canvas>
                    </div>
                    <div class="chart-container">
                        <h3>Request Volume</h3>
                        <canvas id="count-chart"></canvas>
                    </div>
                </div>
                <div class="chart-row">
                    <div class="chart-container">
                        <h3>Error Rate by Endpoint</h3>
                        <canvas id="error-chart"></canvas>
                    </div>
                    <div class="chart-container">
                        <h3>Slowest Endpoints (P95)</h3>
                        <canvas id="slow-chart"></canvas>
                    </div>
                </div>
                <div class="chart-container" id="history-chart-container">
                    <h3>Latency History: <span id="history-endpoint"></span></h3>
                    <canvas id="history-chart"></canvas>
                </div>
                <table id="telemetry-table">
                    <thead><tr><th>Endpoint</th><th>Method</th><th>Count</th><th>Avg (ms)</th><th>P50 (ms)</th><th>P95 (ms)</th><th>P99 (ms)</th><th>Error %</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div id="user-tiers-tab" style="display:none">
                <div class="filter-row">
                    <input type="text" id="user-search" placeholder="Search by username or ID..." style="flex:1;max-width:400px" onkeyup="if(event.key==='Enter')searchUsers()">
                    <button class="refresh-btn" onclick="searchUsers()">Search</button>
                </div>
                <div id="user-search-results" style="margin-bottom:24px"></div>
                <div id="user-details" style="display:none">
                    <div class="card" style="margin-bottom:16px">
                        <h3>USER DETAILS</h3>
                        <div id="user-info" style="margin-top:12px"></div>
                    </div>
                    <div class="card" style="margin-bottom:16px">
                        <h3>CHANGE TIER</h3>
                        <div style="display:flex;gap:12px;margin-top:12px;flex-wrap:wrap">
                            <select id="tier-select" style="padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
                                <option value="free">Free</option>
                                <option value="alpha">Alpha</option>
                                <option value="beta">Beta</option>
                                <option value="premium">Premium</option>
                                <option value="staff">Staff</option>
                            </select>
                            <button class="refresh-btn" onclick="updateUserTier()">Update Tier</button>
                        </div>
                    </div>
                    <div class="card">
                        <h3>BADGES</h3>
                        <div id="user-badges" style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap"></div>
                        <div style="display:flex;gap:12px;margin-top:16px;flex-wrap:wrap">
                            <select id="badge-select" style="padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
                                <option value="alpha_tester">Alpha Tester</option>
                                <option value="early_supporter">Early Supporter</option>
                                <option value="staff">Staff</option>
                                <option value="verified">Verified</option>
                                <option value="bug_hunter">Bug Hunter</option>
                                <option value="contributor">Contributor</option>
                                <option value="moderator">Moderator</option>
                                <option value="partner">Partner</option>
                            </select>
                            <button class="refresh-btn" onclick="addBadge()" style="background:#4ade80">Add Badge</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        const token = localStorage.getItem('plexichat-admin-token');
        let latencyChart = null, countChart = null, historyChart = null, errorChart = null, slowChart = null;
        let telemetryData = [];
        
        if (!token) {
            window.location.replace('/api/v1/admin');
        }
        
        function formatEndpoint(endpoint) {
            // Decode URL-encoded emojis and normalize display
            try {
                let decoded = decodeURIComponent(endpoint);
                // Replace {id} placeholders with cleaner display
                decoded = decoded.replace(/\\{id\\}/g, ':id');
                // Replace {emoji} placeholder
                decoded = decoded.replace(/\\{emoji\\}/g, ':emoji');
                return decoded;
            } catch (e) {
                return endpoint;
            }
        }
        
        function shortEndpoint(endpoint) {
            // Shorten endpoint for chart labels
            let short = formatEndpoint(endpoint).replace('/api/v1', '');
            if (short.length > 25) short = short.substring(0, 22) + '...';
            return short;
        }
        
        function getLatencyClass(ms) {
            if (ms < 100) return 'latency-good';
            if (ms < 500) return 'latency-warn';
            return 'latency-bad';
        }
        
        function getLatencyColor(ms) {
            if (ms < 100) return '#4ade80';
            if (ms < 500) return '#fbbf24';
            return '#ef4444';
        }
        
        function getValueClass(ms) {
            if (ms < 100) return 'good';
            if (ms < 500) return 'warn';
            return 'bad';
        }
        
        async function loadTelemetryStats() {
            const hours = document.getElementById('time-range')?.value || 24;
            const source = document.getElementById('telemetry-source')?.value || '';
            try {
                let url = `/api/v1/admin/telemetry/stats?hours=${hours}`;
                if (source) url += `&source=${source}`;
                
                const res = await fetch(url, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) return;
                const data = await res.json();
                telemetryData = data.stats || [];
                renderTelemetrySummary();
                renderTelemetryTable();
                renderCharts();
            } catch (e) {
                console.error('Failed to load telemetry stats:', e);
            }
        }
        
        function renderTelemetrySummary() {
            const container = document.getElementById('telemetry-summary');
            if (!telemetryData.length) {
                container.innerHTML = '';
                return;
            }
            
            const totalRequests = telemetryData.reduce((sum, t) => sum + t.count, 0);
            const avgLatency = telemetryData.reduce((sum, t) => sum + t.avg_ms * t.count, 0) / totalRequests;
            const maxP95 = Math.max(...telemetryData.map(t => t.p95_ms));
            const avgErrorRate = telemetryData.reduce((sum, t) => sum + t.error_rate * t.count, 0) / totalRequests;
            const slowEndpoints = telemetryData.filter(t => t.avg_ms > 500).length;
            const errorEndpoints = telemetryData.filter(t => t.error_rate > 5).length;
            
            container.innerHTML = `
                <div class="card"><h3>Total Requests</h3><div class="value">${totalRequests.toLocaleString()}</div></div>
                <div class="card"><h3>Avg Latency</h3><div class="value ${getValueClass(avgLatency)}">${avgLatency.toFixed(0)}ms</div></div>
                <div class="card"><h3>Max P95</h3><div class="value ${getValueClass(maxP95)}">${maxP95.toFixed(0)}ms</div></div>
                <div class="card"><h3>Avg Error Rate</h3><div class="value ${avgErrorRate > 5 ? 'bad' : avgErrorRate > 1 ? 'warn' : 'good'}">${avgErrorRate.toFixed(1)}%</div></div>
                <div class="card"><h3>Slow Endpoints</h3><div class="value ${slowEndpoints > 0 ? 'bad' : 'good'}">${slowEndpoints}</div><div class="subtitle">&gt;500ms avg</div></div>
                <div class="card"><h3>Error Endpoints</h3><div class="value ${errorEndpoints > 0 ? 'bad' : 'good'}">${errorEndpoints}</div><div class="subtitle">&gt;5% errors</div></div>
            `;
        }
        
        function renderTelemetryTable() {
            const tbody = document.querySelector('#telemetry-table tbody');
            if (!telemetryData.length) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888">No telemetry data</td></tr>';
                return;
            }
            // Sort by avg latency descending for visibility of slow endpoints
            const sorted = [...telemetryData].sort((a, b) => b.avg_ms - a.avg_ms);
            tbody.innerHTML = sorted.map((t, i) => {
                const displayEndpoint = formatEndpoint(t.endpoint);
                const avgClass = getLatencyClass(t.avg_ms);
                const p95Class = getLatencyClass(t.p95_ms);
                const errorClass = t.error_rate > 10 ? 'latency-bad' : (t.error_rate > 1 ? 'latency-warn' : 'latency-good');
                return `<tr class="endpoint-row" onclick="showHistory('${encodeURIComponent(t.endpoint)}', '${t.method}', ${i})">
                    <td class="emoji-cell">${displayEndpoint}</td>
                    <td>${t.method}</td>
                    <td>${t.count.toLocaleString()}</td>
                    <td class="${avgClass}">${t.avg_ms.toFixed(1)}</td>
                    <td>${(t.p50_ms || 0).toFixed(1)}</td>
                    <td class="${p95Class}">${t.p95_ms.toFixed(1)}</td>
                    <td>${(t.p99_ms || 0).toFixed(1)}</td>
                    <td class="${errorClass}">${t.error_rate.toFixed(1)}%</td>
                </tr>`;
            }).join('');
        }
        
        function renderCharts() {
            if (!telemetryData.length) return;
            
            // Sort by request count for main charts
            const byCount = [...telemetryData].sort((a, b) => b.count - a.count).slice(0, 10);
            // Sort by P95 latency for slow endpoints chart
            const byP95 = [...telemetryData].sort((a, b) => b.p95_ms - a.p95_ms).slice(0, 8);
            // Sort by error rate for error chart
            const byError = [...telemetryData].filter(t => t.error_rate > 0).sort((a, b) => b.error_rate - a.error_rate).slice(0, 8);
            
            const labels = byCount.map(t => shortEndpoint(t.endpoint));
            const avgData = byCount.map(t => t.avg_ms);
            const p95Data = byCount.map(t => t.p95_ms);
            const countData = byCount.map(t => t.count);
            
            const chartOptions = {
                responsive: true,
                maintainAspectRatio: true,
                plugins: { legend: { labels: { color: '#eaeaea' } } },
                scales: {
                    x: { ticks: { color: '#888', maxRotation: 45 }, grid: { color: '#0f3460' } },
                    y: { ticks: { color: '#888' }, grid: { color: '#0f3460' } }
                }
            };
            
            const horizontalOptions = {
                ...chartOptions,
                indexAxis: 'y',
                scales: {
                    x: { ticks: { color: '#888' }, grid: { color: '#0f3460' } },
                    y: { ticks: { color: '#888', font: { size: 10 } }, grid: { color: '#0f3460' } }
                }
            };
            
            // Latency chart - Avg vs P95 comparison
            if (latencyChart) latencyChart.destroy();
            latencyChart = new Chart(document.getElementById('latency-chart'), {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        { label: 'Avg (ms)', data: avgData, backgroundColor: '#3b82f6', borderRadius: 4 },
                        { label: 'P95 (ms)', data: p95Data, backgroundColor: '#e94560', borderRadius: 4 }
                    ]
                },
                options: horizontalOptions
            });
            
            // Request count chart - Doughnut
            if (countChart) countChart.destroy();
            countChart = new Chart(document.getElementById('count-chart'), {
                type: 'doughnut',
                data: {
                    labels,
                    datasets: [{ 
                        data: countData, 
                        backgroundColor: ['#e94560','#3b82f6','#4ade80','#fbbf24','#8b5cf6','#06b6d4','#f97316','#ec4899','#84cc16','#6366f1']
                    }]
                },
                options: { 
                    responsive: true, 
                    plugins: { 
                        legend: { position: 'right', labels: { color: '#eaeaea', font: { size: 10 } } }
                    }
                }
            });
            
            // Error rate chart
            if (errorChart) errorChart.destroy();
            if (byError.length > 0) {
                errorChart = new Chart(document.getElementById('error-chart'), {
                    type: 'bar',
                    data: {
                        labels: byError.map(t => shortEndpoint(t.endpoint)),
                        datasets: [{
                            label: 'Error Rate %',
                            data: byError.map(t => t.error_rate),
                            backgroundColor: byError.map(t => t.error_rate > 10 ? '#ef4444' : t.error_rate > 5 ? '#fbbf24' : '#4ade80'),
                            borderRadius: 4
                        }]
                    },
                    options: horizontalOptions
                });
            } else {
                // No errors - show empty state
                const ctx = document.getElementById('error-chart').getContext('2d');
                ctx.fillStyle = '#888';
                ctx.font = '14px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('No errors recorded', ctx.canvas.width / 2, ctx.canvas.height / 2);
            }
            
            // Slowest endpoints chart (P95)
            if (slowChart) slowChart.destroy();
            slowChart = new Chart(document.getElementById('slow-chart'), {
                type: 'bar',
                data: {
                    labels: byP95.map(t => shortEndpoint(t.endpoint)),
                    datasets: [{
                        label: 'P95 Latency (ms)',
                        data: byP95.map(t => t.p95_ms),
                        backgroundColor: byP95.map(t => getLatencyColor(t.p95_ms)),
                        borderRadius: 4
                    }]
                },
                options: horizontalOptions
            });
        }
        
        async function showHistory(endpoint, method, rowIndex) {
            document.querySelectorAll('.endpoint-row').forEach((r, i) => r.classList.toggle('selected', i === rowIndex));
            document.getElementById('history-chart-container').style.display = 'block';
            // Decode the endpoint for display (it was encoded in the onclick)
            const decodedEndpoint = decodeURIComponent(endpoint);
            document.getElementById('history-endpoint').textContent = formatEndpoint(decodedEndpoint) + ' (' + method + ')';
            
            const hours = document.getElementById('time-range')?.value || 24;
            const bucketMinutes = hours <= 6 ? 5 : hours <= 24 ? 15 : 60;
            
            try {
                const res = await fetch(`/api/v1/admin/telemetry/history?endpoint=${endpoint}&method=${method}&hours=${hours}&bucket_minutes=${bucketMinutes}`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) return;
                const data = await res.json();
                const history = data.history || [];
                
                if (history.length === 0) {
                    document.getElementById('history-chart-container').innerHTML = '<h3>Latency History: ' + formatEndpoint(decodedEndpoint) + '</h3><p style="color:#888;text-align:center;padding:40px">No history data available</p><canvas id="history-chart"></canvas>';
                    return;
                }
                
                if (historyChart) historyChart.destroy();
                historyChart = new Chart(document.getElementById('history-chart'), {
                    type: 'line',
                    data: {
                        labels: history.map(h => {
                            const d = new Date(h.timestamp);
                            return hours <= 24 ? d.toLocaleTimeString() : d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                        }),
                        datasets: [
                            { label: 'Avg (ms)', data: history.map(h => h.avg_response_time_ms), borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', tension: 0.3, fill: true },
                            { label: 'Max (ms)', data: history.map(h => h.max_response_time_ms), borderColor: '#e94560', tension: 0.3, fill: false, borderDash: [5,5] },
                            { label: 'Min (ms)', data: history.map(h => h.min_response_time_ms), borderColor: '#4ade80', tension: 0.3, fill: false, borderDash: [2,2] }
                        ]
                    },
                    options: {
                        responsive: true,
                        interaction: { intersect: false, mode: 'index' },
                        plugins: { 
                            legend: { labels: { color: '#eaeaea' } },
                            tooltip: {
                                callbacks: {
                                    afterBody: function(context) {
                                        const idx = context[0].dataIndex;
                                        return 'Requests: ' + history[idx].count;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: { ticks: { color: '#888', maxTicksLimit: 12, maxRotation: 45 }, grid: { color: '#0f3460' } },
                            y: { ticks: { color: '#888' }, grid: { color: '#0f3460' }, title: { display: true, text: 'Latency (ms)', color: '#888' } }
                        }
                    }
                });
            } catch (e) {
                console.error('Failed to load history:', e);
            }
        }
        
        async function verifyAndLoad() {
            try {
                const res = await fetch('/api/v1/admin/dashboard', { 
                    headers: { 'Authorization': 'Bearer ' + token },
                    method: 'GET'
                });
                
                if (res.status === 401) {
                    localStorage.removeItem('plexichat-admin-token');
                    window.location.replace('/api/v1/admin');
                    return;
                }
                
                if (!res.ok) throw new Error('Failed to load dashboard');
                
                document.getElementById('loading').style.display = 'none';
                document.getElementById('content').style.display = 'block';
                
                const data = await res.json();
                document.getElementById('stats').innerHTML = `
                    <div class="card"><h3>Open</h3><div class="value">${data.tickets.open}</div></div>
                    <div class="card"><h3>In Progress</h3><div class="value">${data.tickets.in_progress}</div></div>
                    <div class="card"><h3>Resolved</h3><div class="value">${data.tickets.resolved}</div></div>
                    <div class="card"><h3>Total</h3><div class="value">${data.tickets.total}</div></div>
                `;
                
                const ticketsRes = await fetch('/api/v1/admin/tickets?limit=20', { 
                    headers: { 'Authorization': 'Bearer ' + token } 
                });
                if (ticketsRes.status === 401) {
                    localStorage.removeItem('plexichat-admin-token');
                    window.location.replace('/api/v1/admin');
                    return;
                }
                const tickets = await ticketsRes.json();
                document.querySelector('#tickets-table tbody').innerHTML = tickets.map(t => 
                    `<tr><td>${t.id}</td><td>${t.username}</td><td>${t.category || '-'}</td><td><span class="status ${t.status}">${t.status}</span></td><td>${new Date(t.created_at).toLocaleString()}</td></tr>`
                ).join('') || '<tr><td colspan="5" style="text-align:center;color:#888">No tickets</td></tr>';
                
                // Load full telemetry stats for charts
                await loadTelemetryStats();
            } catch (e) { 
                document.getElementById('loading').style.display = 'none';
                document.getElementById('error').style.display = 'block'; 
                document.getElementById('error').textContent = 'Error loading data: ' + e.message; 
            }
        }
        
        function showTab(name, btn) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tickets-tab').style.display = name === 'tickets' ? 'block' : 'none';
            document.getElementById('telemetry-tab').style.display = name === 'telemetry' ? 'block' : 'none';
            document.getElementById('hash-reports-tab').style.display = name === 'hash-reports' ? 'block' : 'none';
            document.getElementById('user-tiers-tab').style.display = name === 'user-tiers' ? 'block' : 'none';
            if (name === 'telemetry' && !telemetryData.length) loadTelemetryStats();
            if (name === 'hash-reports') loadHashReports();
        }
        
        // ==================== Hash Reports Functions ====================
        let hashReportsData = [];
        let blockedHashesData = [];
        
        async function loadHashReports() {
            const statusFilter = document.getElementById('hash-status-filter')?.value || '';
            try {
                // Load report counts
                const countsRes = await fetch('/api/v1/admin/hash-reports/counts', {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (countsRes.ok) {
                    const counts = await countsRes.json();
                    document.getElementById('hash-report-stats').innerHTML = `
                        <div class="card"><h3>Pending</h3><div class="value ${counts.pending > 0 ? 'warn' : ''}">${counts.pending}</div></div>
                        <div class="card"><h3>Blocked</h3><div class="value">${counts.blocked}</div></div>
                        <div class="card"><h3>Cleared</h3><div class="value">${counts.cleared}</div></div>
                        <div class="card"><h3>Total Reports</h3><div class="value">${counts.total}</div></div>
                    `;
                }
                
                // Load reports
                let url = '/api/v1/admin/hash-reports?limit=50';
                if (statusFilter) url += `&status_filter=${statusFilter}`;
                const reportsRes = await fetch(url, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (reportsRes.ok) {
                    hashReportsData = await reportsRes.json();
                    renderHashReportsTable();
                }
                
                // Load blocked hashes
                const blockedRes = await fetch('/api/v1/admin/blocked-hashes?limit=50', {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (blockedRes.ok) {
                    blockedHashesData = await blockedRes.json();
                    renderBlockedHashesTable();
                }
            } catch (e) {
                console.error('Failed to load hash reports:', e);
            }
        }
        
        function renderHashReportsTable() {
            const tbody = document.querySelector('#hash-reports-table tbody');
            if (!hashReportsData.length) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888">No reports</td></tr>';
                return;
            }
            tbody.innerHTML = hashReportsData.map(r => {
                const shortHash = r.hash_value.substring(0, 16) + '...';
                const statusClass = r.status === 'pending' ? 'warn' : r.status === 'blocked' ? 'bad' : r.status === 'cleared' ? 'good' : '';
                const date = new Date(r.reported_at).toLocaleString();
                const actions = r.status === 'pending' ? `
                    <button onclick="reviewReport(${r.id}, 'block')" style="padding:4px 8px;background:#ef4444;border:none;border-radius:4px;color:white;cursor:pointer;margin-right:4px">Block</button>
                    <button onclick="reviewReport(${r.id}, 'clear')" style="padding:4px 8px;background:#4ade80;border:none;border-radius:4px;color:#000;cursor:pointer;margin-right:4px">Clear</button>
                    <button onclick="reviewReport(${r.id}, 'dismiss')" style="padding:4px 8px;background:#6b7280;border:none;border-radius:4px;color:white;cursor:pointer">Dismiss</button>
                ` : '<span style="color:#888">Reviewed</span>';
                return `<tr>
                    <td style="font-family:monospace;font-size:12px" title="${r.hash_value}">${shortHash}</td>
                    <td>${r.reporter_username || 'Unknown'}</td>
                    <td>${r.reason}</td>
                    <td><span class="status ${r.status}" style="background:${r.status === 'pending' ? '#fbbf24' : r.status === 'blocked' ? '#ef4444' : r.status === 'cleared' ? '#4ade80' : '#6b7280'};color:${r.status === 'pending' || r.status === 'cleared' ? '#000' : '#fff'}">${r.status}</span></td>
                    <td>${date}</td>
                    <td>${actions}</td>
                </tr>`;
            }).join('');
        }
        
        function renderBlockedHashesTable() {
            const tbody = document.querySelector('#blocked-hashes-table tbody');
            if (!blockedHashesData.length) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#888">No blocked hashes</td></tr>';
                return;
            }
            tbody.innerHTML = blockedHashesData.map(h => {
                const shortHash = h.hash_value.substring(0, 16) + '...';
                const date = new Date(h.blocked_at).toLocaleString();
                return `<tr>
                    <td style="font-family:monospace;font-size:12px" title="${h.hash_value}">${shortHash}</td>
                    <td>${h.reason}</td>
                    <td>${date}</td>
                    <td>${h.auto_blocked ? '<span style="color:#fbbf24">Auto</span>' : 'Manual'}</td>
                    <td><button onclick="unblockHash('${h.hash_value}')" style="padding:4px 8px;background:#3b82f6;border:none;border-radius:4px;color:white;cursor:pointer">Unblock</button></td>
                </tr>`;
            }).join('');
        }
        
        async function reviewReport(reportId, action) {
            const notes = action === 'block' ? prompt('Enter reason for blocking (optional):') : null;
            try {
                const res = await fetch(`/api/v1/admin/hash-reports/${reportId}/review`, {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action, notes })
                });
                if (!res.ok) throw new Error('Review failed');
                loadHashReports();
            } catch (e) {
                alert('Failed to review report: ' + e.message);
            }
        }
        
        async function unblockHash(hashValue) {
            if (!confirm('Are you sure you want to unblock this hash?')) return;
            try {
                const res = await fetch(`/api/v1/admin/blocked-hashes/${hashValue}`, {
                    method: 'DELETE',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Unblock failed');
                loadHashReports();
            } catch (e) {
                alert('Failed to unblock hash: ' + e.message);
            }
        }
        
        function showBlockHashModal() {
            document.getElementById('block-hash-modal').style.display = 'flex';
        }
        
        function hideBlockHashModal() {
            document.getElementById('block-hash-modal').style.display = 'none';
            document.getElementById('block-hash-value').value = '';
            document.getElementById('block-hash-reason').value = '';
        }
        
        async function blockHashManually() {
            const hashValue = document.getElementById('block-hash-value').value.trim();
            const reason = document.getElementById('block-hash-reason').value.trim();
            if (!hashValue || hashValue.length < 64) {
                alert('Please enter a valid SHA-256 hash (64 characters)');
                return;
            }
            if (!reason) {
                alert('Please enter a reason');
                return;
            }
            try {
                const res = await fetch('/api/v1/admin/blocked-hashes', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hash_value: hashValue, reason })
                });
                if (!res.ok) throw new Error('Block failed');
                hideBlockHashModal();
                loadHashReports();
            } catch (e) {
                alert('Failed to block hash: ' + e.message);
            }
        }
        
        async function exportStats() {
            const hours = document.getElementById('time-range')?.value || 24;
            const format = document.getElementById('export-format')?.value || 'json';
            try {
                const res = await fetch(`/api/v1/admin/telemetry/export?format=${format}&hours=${hours}`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Export failed');
                
                if (format === 'json') {
                    const data = await res.json();
                    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `telemetry_${new Date().toISOString().slice(0,19).replace(/[T:]/g, '-')}.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                } else {
                    const blob = await res.blob();
                    const disposition = res.headers.get('Content-Disposition');
                    let filename = `telemetry_export.${format}`;
                    if (disposition) {
                        const match = disposition.match(/filename=([^;]+)/);
                        if (match) filename = match[1];
                    }
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(url);
                }
            } catch (e) {
                alert('Export failed: ' + e.message);
            }
        }
        
        async function resetStats() {
            if (!confirm('Are you sure you want to reset ALL telemetry statistics? This cannot be undone.')) return;
            try {
                const res = await fetch('/api/v1/admin/telemetry/reset', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                const data = await res.json();
                if (data.success) {
                    alert(`Reset complete. Deleted ${data.deleted_count} records.`);
                    loadTelemetryStats();
                } else {
                    alert('Reset failed: ' + (data.message || 'Unknown error'));
                }
            } catch (e) {
                alert('Reset failed: ' + e.message);
            }
        }
        
        // ==================== User Tier Functions ====================
        let selectedUserId = null;
        let selectedUserData = null;
        
        async function searchUsers() {
            const query = document.getElementById('user-search').value.trim();
            if (!query || query.length < 2) {
                document.getElementById('user-search-results').innerHTML = '<p style="color:#888">Enter at least 2 characters to search</p>';
                return;
            }
            
            try {
                const res = await fetch(`/api/v1/admin/users/search?q=${encodeURIComponent(query)}&limit=20`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Search failed');
                const data = await res.json();
                
                if (!data.users || data.users.length === 0) {
                    document.getElementById('user-search-results').innerHTML = '<p style="color:#888">No users found</p>';
                    return;
                }
                
                let html = '<table><thead><tr><th>ID</th><th>Username</th><th>Tier</th><th>Badges</th><th>Actions</th></tr></thead><tbody>';
                for (const user of data.users) {
                    const badges = user.badges.length > 0 ? user.badges.join(', ') : '-';
                    html += `<tr>
                        <td style="font-family:monospace;font-size:12px">${user.id}</td>
                        <td>${escapeHtml(user.username)}</td>
                        <td><span class="status" style="background:var(--accent)">${user.tier}</span></td>
                        <td style="font-size:12px">${escapeHtml(badges)}</td>
                        <td><button class="refresh-btn" onclick="selectUser('${user.id}')" style="padding:4px 12px;font-size:12px">Edit</button></td>
                    </tr>`;
                }
                html += '</tbody></table>';
                document.getElementById('user-search-results').innerHTML = html;
            } catch (e) {
                document.getElementById('user-search-results').innerHTML = `<p style="color:var(--bad)">Error: ${e.message}</p>`;
            }
        }
        
        async function selectUser(userId) {
            selectedUserId = userId;
            try {
                const res = await fetch(`/api/v1/admin/users/${userId}`, {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Failed to load user');
                selectedUserData = await res.json();
                
                document.getElementById('user-info').innerHTML = `
                    <p><strong>ID:</strong> <span style="font-family:monospace">${selectedUserData.id}</span></p>
                    <p><strong>Username:</strong> ${escapeHtml(selectedUserData.username)}</p>
                    <p><strong>Email:</strong> ${selectedUserData.email || '-'}</p>
                    <p><strong>Current Tier:</strong> <span class="status" style="background:var(--accent)">${selectedUserData.tier}</span></p>
                    <p><strong>Created:</strong> ${new Date(selectedUserData.created_at).toLocaleString()}</p>
                `;
                
                document.getElementById('tier-select').value = selectedUserData.tier;
                renderUserBadges();
                document.getElementById('user-details').style.display = 'block';
            } catch (e) {
                alert('Error loading user: ' + e.message);
            }
        }
        
        function renderUserBadges() {
            const container = document.getElementById('user-badges');
            if (!selectedUserData.badges || selectedUserData.badges.length === 0) {
                container.innerHTML = '<span style="color:#888">No badges</span>';
                return;
            }
            container.innerHTML = selectedUserData.badges.map(badge => 
                `<span class="status" style="background:var(--good);color:#000;cursor:pointer" onclick="removeBadge('${badge}')" title="Click to remove">${badge} ×</span>`
            ).join('');
        }
        
        async function updateUserTier() {
            if (!selectedUserId) return;
            const tier = document.getElementById('tier-select').value;
            
            try {
                const res = await fetch(`/api/v1/admin/users/${selectedUserId}/tier`, {
                    method: 'PUT',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tier })
                });
                if (!res.ok) throw new Error('Failed to update tier');
                
                selectedUserData.tier = tier;
                document.getElementById('user-info').querySelector('.status').textContent = tier;
                alert('Tier updated successfully!');
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        async function addBadge() {
            if (!selectedUserId) return;
            const badge = document.getElementById('badge-select').value;
            
            if (selectedUserData.badges.includes(badge)) {
                alert('User already has this badge');
                return;
            }
            
            try {
                const res = await fetch(`/api/v1/admin/users/${selectedUserId}/badges/${badge}`, {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Failed to add badge');
                const data = await res.json();
                
                selectedUserData.badges = data.badges;
                renderUserBadges();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        async function removeBadge(badge) {
            if (!selectedUserId) return;
            if (!confirm(`Remove badge "${badge}" from this user?`)) return;
            
            try {
                const res = await fetch(`/api/v1/admin/users/${selectedUserId}/badges/${badge}`, {
                    method: 'DELETE',
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!res.ok) throw new Error('Failed to remove badge');
                const data = await res.json();
                
                selectedUserData.badges = data.badges;
                renderUserBadges();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function logout() {
            fetch('/api/v1/admin/logout', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token } });
            localStorage.removeItem('plexichat-admin-token');
            window.location.replace('/api/v1/admin');
        }
        
        verifyAndLoad();
    </script>
</body>
</html>
"""


@router.get(
    "",
    response_class=HTMLResponse,
    summary="Admin login page",
    responses={
        200: {"description": "Admin login page HTML"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
    },
)
async def admin_login_page(request: Request):
    """Serve the admin login page."""
    _check_host_restriction(request)
    return HTMLResponse(content=ADMIN_LOGIN_HTML)


@router.get(
    "/ui",
    response_class=HTMLResponse,
    summary="Admin dashboard page",
    responses={
        200: {"description": "Admin dashboard page HTML"},
        403: {"model": ErrorResponse, "description": "Access denied (host restriction)"},
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
    },
)
async def admin_dashboard_page(request: Request):
    """Serve the admin dashboard page."""
    _check_host_restriction(request)
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML)
