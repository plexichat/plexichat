"""
Admin API routes.

Provides admin-only endpoints for server management.
Host-restricted by default to localhost only.

SECURITY WARNING: Disabling host_restriction allows ANYONE to access the admin
panel and view potentially sensitive user data including feedback, telemetry,
and system statistics. Only disable this if you have other security measures
in place (VPN, firewall, reverse proxy auth, etc.)
"""

from fastapi import APIRouter, HTTPException, status, Request, Response
from fastapi.responses import HTMLResponse
from typing import Dict, List, Optional, Union, Any
import time
import os
from pathlib import Path

import src.api as api
import utils.config as config
import utils.logger as logger
from src.utils.security import generate_csp_nonce, build_admin_csp_header
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
    TelemetryHistoryBucket,
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
    IPBlockRequest,
    BlockedIPResponse,
    ForceLogoutRequest,
    UserLockRequest,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["Admin Management"])


# ==================== Security Tools Routes ====================


@router.get(
    "/security/blocked-ips",
    response_model=List[BlockedIPResponse],
    summary="Get blocked IPs",
)
async def get_blocked_ips(request: Request) -> List[BlockedIPResponse]:
    """Get list of blocked IP addresses."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import auth
    return [BlockedIPResponse(**ip) for ip in auth.get_blocked_ips()]


@router.post(
    "/security/block-ip",
    response_model=SuccessResponse,
    summary="Block an IP address",
)
async def block_ip(request: Request, body: IPBlockRequest) -> SuccessResponse:
    """Block an IP address."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import auth
    auth.block_ip(body.ip_address, body.reason, admin_id, body.duration_hours)
    return SuccessResponse(success=True)


@router.delete(
    "/security/unblock-ip/{ip_address:path}",
    response_model=SuccessResponse,
    summary="Unblock an IP address",
)
async def unblock_ip(request: Request, ip_address: str) -> SuccessResponse:
    """Unblock an IP address."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import auth
    auth.unblock_ip(ip_address)
    return SuccessResponse(success=True)


@router.post(
    "/security/force-logout",
    response_model=SuccessResponse,
    summary="Force logout a user everywhere",
)
async def force_logout(request: Request, body: ForceLogoutRequest) -> SuccessResponse:
    """Invalidate all sessions for a specific user and notify them via WebSocket."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    logger.debug(f"Force logout requested for user_id raw: {body.user_id!r}")

    try:
        user_id = int(body.user_id)
    except (ValueError, TypeError) as e:
        logger.warning(f"Invalid user ID format for force logout: {body.user_id!r} - {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID format"}},
        )

    from src.core import auth
    auth.logout_all(user_id)

    # Broadcast security logout event
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if ws_is_setup():
            dispatcher = get_dispatcher()
            event = Event(
                event_type=EventType.SECURITY_LOGOUT,
                data={
                    "user_id": str(user_id),
                    "message": "As a security precaution, you have been logged out of all devices.",
                }
            )
            await dispatcher.dispatch_event(event, [user_id])
    except Exception as e:
        logger.error(f"Failed to broadcast force logout: {e}")

    return SuccessResponse(success=True)


@router.post(
    "/security/lock-user",
    response_model=SuccessResponse,
    summary="Lock/Suspend a user account",
)
async def admin_lock_user(request: Request, body: UserLockRequest) -> SuccessResponse:
    """Lock/suspend a user account and logout all sessions."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        user_id = int(body.user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID format"}},
        )

    from src.core import admin
    admin.lock_user(user_id, body.duration_seconds)

    # Broadcast security logout event
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if ws_is_setup():
            dispatcher = get_dispatcher()
            msg = "Your account has been suspended."
            if body.duration_seconds:
                # Format duration roughly
                if body.duration_seconds >= 86400:
                    msg += f" Suspension expires in {body.duration_seconds // 86400} days."
                elif body.duration_seconds >= 3600:
                    msg += f" Suspension expires in {body.duration_seconds // 3600} hours."
                else:
                    msg += f" Suspension expires in {body.duration_seconds} seconds."
            else:
                msg += " This suspension is permanent."

            event = Event(
                event_type=EventType.SECURITY_LOGOUT,
                data={
                    "user_id": str(user_id),
                    "message": msg,
                }
            )
            await dispatcher.dispatch_event(event, [user_id])
    except Exception as e:
        logger.error(f"Failed to broadcast lock user logout: {e}")

    logger.info(f"Admin {admin_id} locked user {user_id} (duration: {body.duration_seconds})")
    return SuccessResponse(success=True)


@router.post(
    "/security/unlock-user",
    response_model=SuccessResponse,
    summary="Unlock/Unsuspend a user account",
)
async def admin_unlock_user(request: Request, body: ForceLogoutRequest) -> SuccessResponse:
    """Unlock/unsuspend a user account."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        user_id = int(body.user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID format"}},
        )

    from src.core import admin
    admin.unlock_user(user_id)

    logger.info(f"Admin {admin_id} unlocked user {user_id}")
    return SuccessResponse(success=True)


@router.post(
    "/security/logout-all",
    response_model=SuccessResponse,
    summary="Logout ALL users everywhere",
)
async def logout_all_users(request: Request) -> SuccessResponse:
    """Invalidate ALL active sessions for ALL users."""
    _check_host_restriction(request)
    _get_admin_from_token(request)

    from src.core import auth
    auth.logout_all_users()

    # In a real scenario, we might want to broadcast to everyone, but closing WS connections is more effective
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        if ws_is_setup():
            dispatcher = get_dispatcher()
            await dispatcher.close_all_connections(
                close_code=4004, 
                reason="Site-wide security reset. Please log in again."
            )
    except Exception as e:
        logger.error(f"Failed to close all connections: {e}")

    return SuccessResponse(success=True)


def _check_host_restriction(request: Request) -> None:
    """Check if client IP is allowed to access admin UI."""
    # Bypass all restrictions for secure self-test requests
    is_selftest = request.scope.get("state", {}).get("is_selftest", False)
    if not is_selftest:
        is_selftest = getattr(request.state, "is_selftest", False)
    
    # Check for internal secret as a reliable bypass for local automation
    if not is_selftest:
        internal_secret = api.get_internal_secret()
        provided_secret = request.headers.get("X-Plexichat-Internal-Secret")
        is_selftest = internal_secret and provided_secret == internal_secret

    if is_selftest:
        logger.info(
            f"Admin host restriction bypass (is_selftest=True): {request.method} {request.url.path}"
        )
        return

    admin_config = config.get("admin_ui", {})
    if not admin_config.get("enabled", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Not found"}},
        )

    # Extract real client IP using consolidated utility which handles trusted proxies securely
    from src.utils.net import get_client_ip
    client_ip = get_client_ip(request) or "unknown"

    # Check for explicitly blocked IPs or prefixes (e.g. to block tunnels)
    blocked_ips = admin_config.get("blocked_ips", [])
    for blocked in blocked_ips:
        if client_ip == blocked or (blocked.endswith(".") and client_ip.startswith(blocked)):
            logger.warning(f"Admin access blocked from configured blocked IP/prefix: {client_ip} (matches {blocked})")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Access from this network is restricted"}},
            )

    host_restriction = admin_config.get("host_restriction", {})
    if host_restriction.get("enabled", True):
        # Security: Additional verification for trust_x_forwarded_for configuration
        api_config = config.get("api", {})
        if api_config.get("trust_x_forwarded_for", False):
            trusted_proxies = api_config.get("trusted_proxies", [])

            # Fail closed: if trust is enabled but no proxies are defined, deny access
            if not trusted_proxies:
                logger.error("Admin access blocked: api.trust_x_forwarded_for is enabled but api.trusted_proxies is empty (fail-closed).")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": "Security configuration error: Proxy trust mismatch"}},
                )

            # Warn if wildcard is used
            if "*" in trusted_proxies:
                logger.warning("SECURITY WARNING: Admin access allowed using wildcard (*) trusted proxy. This allows IP spoofing.")
            else:
                # If trust is on, but we're not using wildcard, check if the direct client is trusted
                # Note: get_client_ip already does this, but we reinforce it here for the admin UI specifically
                direct_ip = request.client.host if request.client else "unknown"
                if direct_ip not in trusted_proxies and direct_ip not in ("127.0.0.1", "localhost", "::1"):
                    logger.warning(f"Admin access denied: direct client {direct_ip} is not in trusted_proxies.")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={"error": {"code": 403, "message": "Access denied: Untrusted proxy"}},
                    )

        allowed_hosts = host_restriction.get(
            "allowed_hosts", ["127.0.0.1", "localhost", "::1"]
        )
        from src.core import admin
        if not admin.check_host_restriction(client_ip, allowed_hosts):
            logger.warning(f"Admin access denied from {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

    # Check origin if allowed_origins is configured
    allowed_origins = admin_config.get("allowed_origins", [])
    if allowed_origins:
        origin = request.headers.get("origin", "")
        if origin and origin not in allowed_origins:
            logger.warning(
                f"Admin access denied - origin {origin} not in allowed_origins"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Origin not allowed"}},
            )


def _load_admin_template(template_name: str, csp_nonce: Optional[str] = None) -> str:
    """Load an admin UI template from the templates directory with path sanitization and CSP nonce injection."""
    # Security: prevent path traversal by only allowing specific template filenames
    allowed_templates = ["login.html", "dashboard.html"]
    if template_name not in allowed_templates:
        logger.warning(f"Blocked unauthorized admin template access: {template_name}")
        return "<h1>Access Denied</h1><p>Unauthorized template requested.</p>"

    # Template directory is relative to this file
    template_dir = Path(__file__).parent.parent / "templates" / "admin"
    template_path = (template_dir / template_name).resolve()

    # Security: Ensure resolved path is within the template directory
    if not str(template_path).startswith(str(template_dir.resolve())):
        logger.warning(f"Blocked path traversal attempt for template: {template_name}")
        return "<h1>Access Denied</h1><p>Unauthorized path requested.</p>"

    if not template_path.exists():
        logger.error(f"Admin template not found: {template_path}")
        return "<h1>Template Error</h1><p>Admin UI template not found.</p>"

    try:
        content = template_path.read_text(encoding="utf-8")
        
        # Inject CSP nonce into template if provided
        if csp_nonce:
            # Replace <script> with <script nonce="...">
            content = content.replace("<script>", f'<script nonce="{csp_nonce}">')
            # Replace any placeholders if they exist
            content = content.replace("{{ csp_nonce }}", csp_nonce)
            
        return content
    except Exception as e:
        logger.error(f"Error reading admin template {template_name}: {e}")
        return f"<h1>Template Error</h1><p>Failed to load template: {e}</p>"


def _get_admin_from_token(request: Request) -> int:
    """Get admin ID from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": 401, "message": "Invalid token"}},
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

        if has_permission(user.permissions, "admin.*") or has_permission(
            user.permissions, "*"
        ):
            # Return user_id but treat as admin
            logger.debug(f"Allowing admin access via internal secret for user {user.user_id}")
            return user.user_id

    from src.core import admin

    admin_id = admin.validate_session(token)

    if not admin_id:
        logger.warning(f"Admin session validation failed for token starting with: {token[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": 401, "message": "Invalid or expired token"}},
        )

    return admin_id


# ==================== Auth Routes ====================


@router.post(
    "/login",
    response_model=AdminLoginResponse,
    summary="Admin login",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_login(
    request: Request, login_data: AdminLoginRequest
) -> AdminLoginResponse:
    """Admin login endpoint."""
    _check_host_restriction(request)

    from src.core import admin

    try:
        client_ip = request.client.host if request.client else "unknown"
        result = admin.login(login_data.username, login_data.password, client_ip)

        if not result.success:
            logger.warning(
                f"Admin login failed for user '{login_data.username}' from {client_ip}: {result.error}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": 401, "message": result.error}},
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
                message="Scan the QR code with your authenticator app, then enter the code",
            )

        if result.requires_otp_verify:
            logger.info(f"Admin '{login_data.username}' requires OTP verification")
            return AdminLoginResponse(
                status="otp_required",
                admin_id=str(result.user_id),  # String to avoid JS precision loss
                message="Enter your 2FA code",
            )

        logger.info(f"Admin '{login_data.username}' logged in successfully (default)")
        return AdminLoginResponse(status="success", token=result.token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in admin_login for '{login_data.username}': {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/verify-otp",
    response_model=AdminLoginResponse,
    summary="Verify admin OTP",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid admin_id"},
        401: {"model": ErrorResponse, "description": "Invalid or expired OTP code"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def verify_otp(
    request: Request, otp_data: OTPVerifyRequest
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
            detail={"error": {"code": 400, "message": "Invalid admin_id"}},
        )

    try:
        if otp_data.is_setup:
            result = admin.verify_otp_setup(admin_id, otp_data.code)
        else:
            result = admin.verify_otp(admin_id, otp_data.code)

        if not result.success:
            logger.warning(
                f"Admin OTP verification failed for admin {admin_id}: {result.error}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": 401, "message": result.error}},
            )

        logger.info(f"Admin {admin_id} OTP verified successfully")
        return AdminLoginResponse(status="success", token=result.token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in verify_otp for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/logout",
    response_model=SuccessResponse,
    summary="Admin logout",
    responses={
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Dashboard Routes ====================


@router.get(
    "/dashboard",
    response_model=AdminDashboardResponse,
    summary="Get admin dashboard data",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
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
                        error_rate=round(s.error_rate * 100, 2),
                    )
                    for s in stats[:20]
                ]
        except Exception as te:
            logger.debug(f"Failed to get telemetry stats for dashboard: {te}")

        logger.info(f"Admin {admin_id} retrieved dashboard data")
        return AdminDashboardResponse(tickets=ticket_counts, telemetry=telemetry_stats)
    except Exception as e:
        logger.error(
            f"Failed to get dashboard data for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/tickets",
    response_model=List[TicketResponse],
    summary="Get feedback tickets",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_tickets(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[TicketResponse]:
    """Get feedback tickets."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        tickets = admin.get_feedback_tickets(status_filter, limit, offset)

        logger.debug(
            f"Admin {admin_id} retrieved {len(tickets)} tickets (status={status_filter})"
        )
        return [
            TicketResponse(
                id=str(t.id),
                user_id=str(t.user_id),
                username=t.username,
                content=t.content,
                category=t.category,
                rating=t.rating,
                status=t.status,
                created_at=t.created_at,
                resolved_at=t.resolved_at,
                resolved_by=str(t.resolved_by) if t.resolved_by else None,
            )
            for t in tickets
        ]
    except Exception as e:
        logger.error(f"Failed to get tickets for admin {admin_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketResponse,
    summary="Get a single ticket",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "Ticket not found or Admin UI disabled",
        },
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
                detail={"error": {"code": 404, "message": "Ticket not found"}},
            )

        return TicketResponse(
            id=str(ticket.id),
            user_id=str(ticket.user_id),
            username=ticket.username,
            content=ticket.content,
            category=ticket.category,
            rating=ticket.rating,
            status=ticket.status,
            created_at=ticket.created_at,
            resolved_at=ticket.resolved_at,
            resolved_by=str(ticket.resolved_by) if ticket.resolved_by else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get ticket {ticket_id} for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/tickets/{ticket_id}/status",
    response_model=SuccessResponse,
    summary="Update ticket status",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "Ticket not found or Admin UI disabled",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_ticket_status(
    ticket_id: int, update: TicketStatusUpdate, request: Request
) -> SuccessResponse:
    """Update ticket status."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        if not admin.get_ticket(ticket_id):
            logger.warning(
                f"Ticket {ticket_id} not found for status update (admin {admin_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Ticket not found"}},
            )

        admin.update_ticket_status(ticket_id, update.status, admin_id)
        logger.info(
            f"Admin {admin_id} updated ticket {ticket_id} status to {update.status}"
        )

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to update ticket {ticket_id} status by admin {admin_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/tickets/{ticket_id}/notes",
    response_model=List[NoteResponse],
    summary="Get internal notes for a ticket",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "Ticket not found or Admin UI disabled",
        },
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
            logger.warning(
                f"Ticket {ticket_id} not found for notes retrieval (admin {admin_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Ticket not found"}},
            )

        notes = admin.get_ticket_notes(ticket_id)

        return [
            NoteResponse(
                id=str(n.id),
                ticket_id=str(n.ticket_id),
                admin_id=str(n.admin_id),
                admin_username=n.admin_username,
                content=n.content,
                created_at=n.created_at,
            )
            for n in notes
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get ticket {ticket_id} notes for admin {admin_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/tickets/{ticket_id}/notes",
    response_model=NoteResponse,
    summary="Add internal note to a ticket",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "Ticket not found or Admin UI disabled",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_ticket_note(
    ticket_id: int, note: InternalNoteCreate, request: Request
) -> NoteResponse:
    """Add an internal note to a ticket."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        if not admin.get_ticket(ticket_id):
            logger.warning(
                f"Ticket {ticket_id} not found for note addition (admin {admin_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Ticket not found"}},
            )

        new_note = admin.add_internal_note(ticket_id, admin_id, note.content)

        if new_note is None:
            logger.error(
                f"Failed to create note for ticket {ticket_id} by admin {admin_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to create note"}},
            )

        logger.info(f"Admin {admin_id} added note to ticket {ticket_id}")
        return NoteResponse(
            id=str(new_note.id),
            ticket_id=str(new_note.ticket_id),
            admin_id=str(new_note.admin_id),
            admin_username=new_note.admin_username,
            content=new_note.content,
            created_at=new_note.created_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to add note to ticket {ticket_id} by admin {admin_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/telemetry/stats",
    response_model=TelemetryStatsResponse,
    summary="Get telemetry statistics",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_telemetry_stats(
    request: Request,
    hours: int = 24,
    endpoint: Optional[str] = None,
    source: Optional[str] = None,
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
            logger.info(
                f"Admin {admin_id} requested telemetry stats but telemetry is not setup"
            )
            return TelemetryStatsResponse(stats=[], source=source or "all")

        # Map source to client_id filter
        client_id_filter = None
        if source == "server":
            client_id_filter = "server"
        elif source == "client":
            # For client, we want everything except server
            pass

        stats = telemetry.get_endpoint_stats(
            hours=hours, endpoint_filter=endpoint, client_id_filter=client_id_filter
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
                    error_rate=round(s.error_rate * 100, 2),
                )
                for s in stats
            ],
            source=source or "all",
        )
    except Exception as e:
        logger.error(
            f"Failed to get telemetry stats for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/telemetry/history",
    response_model=TelemetryHistoryResponse,
    summary="Get telemetry history for an endpoint",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_telemetry_history(
    request: Request,
    endpoint: str,
    method: str = "GET",
    hours: int = 24,
    bucket_minutes: int = 5,
) -> TelemetryHistoryResponse:
    """Get telemetry history for an endpoint."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        from src.core import telemetry

        if not telemetry.is_setup():
            logger.info(
                f"Admin {admin_id} requested telemetry history but telemetry is not setup"
            )
            return TelemetryHistoryResponse(history=[])

        history = telemetry.get_response_time_history(
            endpoint=endpoint,
            method=method.upper(),
            hours=hours,
            bucket_minutes=bucket_minutes,
        )

        logger.debug(
            f"Admin {admin_id} retrieved telemetry history for {method} {endpoint}"
        )
        return TelemetryHistoryResponse(
            history=[TelemetryHistoryBucket(**h) for h in history]
        )
    except Exception as e:
        logger.error(
            f"Failed to get telemetry history for {method} {endpoint} (admin {admin_id}): {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/telemetry/reset",
    response_model=TelemetryResetResponse,
    summary="Reset all telemetry statistics",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
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
            logger.info(
                f"Admin {admin_id} requested telemetry reset but telemetry is not setup"
            )
            return TelemetryResetResponse(success=False, deleted_count=0)

        deleted_count = telemetry.reset_all_stats()
        logger.info(
            f"Admin {admin_id} reset telemetry stats, deleted {deleted_count} records"
        )

        return TelemetryResetResponse(success=True, deleted_count=deleted_count)
    except Exception as e:
        logger.error(
            f"Failed to reset telemetry stats by admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/telemetry/export",
    response_model=TelemetryExportResponse,
    summary="Export telemetry statistics",
    responses=    {
        200: {
            "description": "Exported statistics in the requested format",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/TelemetryExportResponse"}
                },
                "text/csv": {"schema": {"type": "string"}},
                "text/plain": {"schema": {"type": "string"}},
                "text/html": {"schema": {"type": "string"}},
            },
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def export_telemetry_stats(
    request: Request, format: str = "json", hours: int = 24
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
            logger.error(
                f"Admin {admin_id} requested telemetry export but telemetry is not setup"
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Telemetry not initialized"}},
            )

        stats = telemetry.get_endpoint_stats(hours=hours)
        export_time = time.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(
            f"Admin {admin_id} exporting telemetry stats (format={format}, hours={hours})"
        )

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
                        error_rate=round(s.error_rate * 100, 2),
                    )
                    for s in stats
                ],
            )

        elif format == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "Endpoint",
                    "Method",
                    "Count",
                    "Avg (ms)",
                    "Min (ms)",
                    "Max (ms)",
                    "P50 (ms)",
                    "P95 (ms)",
                    "P99 (ms)",
                    "Error %",
                ]
            )
            for s in stats:
                writer.writerow(
                    [
                        s.endpoint,
                        s.method,
                        s.count,
                        round(s.avg_response_time_ms, 2),
                        round(s.min_response_time_ms, 2),
                        round(s.max_response_time_ms, 2),
                        round(s.p50_response_time_ms, 2),
                        round(s.p95_response_time_ms, 2),
                        round(s.p99_response_time_ms, 2),
                        round(s.error_rate * 100, 2),
                    ]
                )
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers=    {
                    "Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.csv"
                },
            )

        elif format == "txt":
            lines = [
                "PlexiChat Telemetry Report",
                f"Generated: {export_time}",
                f"Time Range: Last {hours} hours",
                "",
                f"{ 'Endpoint':<50} {'Method':<8} {'Count':>8} {'Avg':>10} {'P95':>10} {'Error%':>8}",
                f"{'-' * 50} {'-' * 8} {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 8}",
            ]
            for s in stats:
                lines.append(
                    f"{s.endpoint[:50]:<50} {s.method:<8} {s.count:>8} {s.avg_response_time_ms:>9.1f}ms {s.p95_response_time_ms:>9.1f}ms {s.error_rate * 100:>7.1f}%"
                )

            # Summary
            if stats:
                total_requests = sum(s.count for s in stats)
                avg_latency = (
                    sum(s.avg_response_time_ms * s.count for s in stats)
                    / total_requests
                    if total_requests
                    else 0
                )
                avg_error = (
                    sum(s.error_rate * s.count for s in stats) / total_requests * 100
                    if total_requests
                    else 0
                )
                lines.extend(
                    [
                        "",
                        "Summary:",
                        f"  Total Requests: {total_requests:,}",
                        f"  Average Latency: {avg_latency:.1f}ms",
                        f"  Average Error Rate: {avg_error:.1f}%",
                        f"  Endpoints Tracked: {len(stats)}",
                    ]
                )

            return Response(
                content="\n".join(lines),
                media_type="text/plain",
                headers=    {
                    "Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.txt"
                },
            )

        elif format == "html":
            # Generate HTML report
            total_requests = sum(s.count for s in stats) if stats else 0
            avg_latency = (
                sum(s.avg_response_time_ms * s.count for s in stats) / total_requests
                if total_requests
                else 0
            )
            avg_error = (
                sum(s.error_rate * s.count for s in stats) / total_requests * 100
                if total_requests
                else 0
            )

            rows_html = ""
            for s in stats:
                latency_class = (
                    "good"
                    if s.avg_response_time_ms < 100
                    else "warn"
                    if s.avg_response_time_ms < 500
                    else "bad"
                )
                error_class = (
                    "good"
                    if s.error_rate < 0.01
                    else "warn"
                    if s.error_rate < 0.05
                    else "bad"
                )
                rows_html += f"""
                <tr>
                    <td>{s.endpoint}</td>
                    <td>{s.method}</td>
                    <td>{s.count:,}</td>
                    <td class="{latency_class}">{s.avg_response_time_ms:.1f}</td>
                    <td>{s.min_response_time_ms:.1f}</td>
                    <td>{s.max_response_time_ms:.1f}</td>
                    <td>{s.p50_ms:.1f}</td>
                    <td class="{latency_class}">{s.p95_ms:.1f}</td>
                    <td>{s.p99_ms:.1f}</td>
                    <td class="{error_class}">{s.error_rate * 100:.1f}%</td>
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
                headers=    {
                    "Content-Disposition": f"attachment; filename=telemetry_{export_time.replace(' ', '_').replace(':', '-')}.html"
                },
            )

        else:
            logger.warning(
                f"Admin {admin_id} requested unsupported export format: {format}"
            )
            raise HTTPException(
                status_code=400,
                detail=    {
                    "error": {
                        "code": 400,
                        "message": f"Unsupported format: {format}. Use json, csv, txt, or html",
                    }
                },
            )

    except HTTPException:
        raise
    except ImportError:
        logger.error(f"Telemetry module not available for admin {admin_id}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    except Exception as e:
        logger.error(
            f"Failed to export telemetry stats for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Hash Reports Routes ====================


@router.get(
    "/hash-reports",
    response_model=List[HashReportResponse],
    summary="Get hash reports",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_hash_reports(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[HashReportResponse]:
    """Get hash reports for admin review with image preview support."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        reports = admin.get_hash_reports(status_filter, limit, offset)
        logger.debug(
            f"Admin {admin_id} retrieved {len(reports)} hash reports (filter={status_filter})"
        )

        return [
            HashReportResponse(
                id=str(r.id),
                hash_value=r.hash_value,
                phash_value=r.phash_value,
                reporter_id=str(r.reporter_id),
                reporter_username=r.reporter_username,
                reason=r.reason,
                details=r.details,
                status=r.status,
                reported_at=r.reported_at,
                reviewed_at=r.reviewed_at,
                reviewed_by=str(r.reviewed_by) if r.reviewed_by else None,
                admin_notes=r.admin_notes,
                uploader_id=str(r.uploader_id) if r.uploader_id else None,
                message_id=str(r.message_id) if r.message_id else None,
                attachment_url=r.attachment_url,
                block_uploader=r.block_uploader,
            )
            for r in reports
        ]
    except Exception as e:
        logger.error(
            f"Failed to get hash reports for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/hash-reports/counts",
    response_model=HashReportCountsResponse,
    summary="Get hash report counts",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
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
        logger.error(
            f"Failed to get hash report counts for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/hash-reports/{report_id}/review",
    response_model=HashReportReviewResponse,
    summary="Review a hash report",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "Report not found or Admin UI disabled",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def review_hash_report(
    report_id: int, review: HashReportReviewRequest, request: Request
) -> HashReportReviewResponse:
    """Review a hash report (block, clear, or dismiss)."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        success = admin.review_hash_report(
            report_id, admin_id, review.action, review.notes
        )

        if not success:
            logger.warning(
                f"Hash report {report_id} not found for review by admin {admin_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Report not found"}},
            )

        logger.info(
            f"Admin {admin_id} reviewed hash report {report_id}: {review.action}"
        )

        return HashReportReviewResponse(success=True, action=review.action)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to review hash report {report_id} by admin {admin_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/blocked-hashes",
    response_model=List[BlockedHashResponse],
    summary="Get blocked hashes",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_blocked_hashes(
    request: Request, limit: int = 100, offset: int = 0
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
                phash_threshold=h.phash_threshold,
            )
            for h in hashes
        ]
    except Exception as e:
        logger.error(
            f"Failed to get blocked hashes for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/blocked-hashes",
    response_model=BlockHashResponse,
    summary="Manually block a hash",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_hash_manually(
    block_request: ManualBlockHashRequest, request: Request
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
            phash_threshold=phash_threshold,
        )

        if not success:
            logger.error(
                f"Failed to manually block {hash_type} hash by admin {admin_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to block hash"}},
            )

        logger.info(
            f"Admin {admin_id} manually blocked {hash_type} hash {block_request.hash_value[:16]}..."
        )

        return BlockHashResponse(
            success=True, hash_value=block_request.hash_value, hash_type=hash_type
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to manually block hash by admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/blocked-hashes/{hash_value}",
    response_model=SuccessResponse,
    summary="Unblock a hash",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unblock_hash(hash_value: str, request: Request) -> SuccessResponse:
    """Unblock a hash."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    from src.core import admin

    try:
        success = admin.unblock_hash(hash_value)

        if not success:
            logger.warning(
                f"Failed to unblock hash {hash_value[:16]}... by admin {admin_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to unblock hash"}},
            )

        logger.info(f"Admin {admin_id} unblocked hash {hash_value[:16]}...")
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to unblock hash {hash_value[:16]}... by admin {admin_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Blocked Users (Media Uploads) ====================


@router.get(
    "/blocked-users",
    response_model=List[BlockedUserResponse],
    summary="Get blocked users",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_blocked_users(
    request: Request, limit: int = 100, offset: int = 0
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
                expires_at=u.expires_at,
            )
            for u in users
        ]
    except Exception as e:
        logger.error(
            f"Failed to get blocked users for admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/blocked-users",
    response_model=BlockUserResponse,
    summary="Block a user from uploading",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_user(
    block_request: BlockUserRequest, request: Request
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
            block_request.duration_hours,
        )

        if not success:
            logger.error(
                f"Failed to block user {block_request.user_id} by admin {admin_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to block user"}},
            )

        logger.info(
            f"Admin {admin_id} blocked user {block_request.user_id} from uploads"
        )

        return BlockUserResponse(success=True, user_id=block_request.user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to block user {block_request.user_id} by admin {admin_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/blocked-users/{user_id}",
    response_model=SuccessResponse,
    summary="Unblock a user from uploading",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unblock_user(user_id: int, request: Request) -> SuccessResponse:
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
                detail={"error": {"code": 500, "message": "Failed to unblock user"}},
            )

        logger.info(f"Admin {admin_id} unblocked user {user_id} from uploads")

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to unblock user {user_id} by admin {admin_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== User Tier Management ====================


@router.get(
    "/users/search",
    response_model=UserSearchListResponse,
    summary="Search users",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_user_search(
    q: str, request: Request, limit: int = 20, offset: int = 0
) -> UserSearchListResponse:
    """Search users by username or ID."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    if not q or len(q) < 2:
        return UserSearchListResponse(users=[])

    try:
        admin_core = api.get_admin()
        if not admin_core:
            logger.error(
                f"Admin module not available for user search (admin {admin_id})"
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

        users_data = admin_core.search_users(q, limit, offset)
        logger.debug(
            f"Admin {admin_id} searched users with query '{q}', found {len(users_data)} results"
        )

        users = []
        for user in users_data:
            users.append(
                UserSearchResponse(
                    id=str(user.id),
                    username=user.username,
                    email=user.email,
                    tier=user.tier or "standard",
                    badges=user.badges,
                    created_at=user.created_at,
                )
            )

        return UserSearchListResponse(users=users)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to search users with query '{q}' (admin {admin_id}): {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/users/{user_id}",
    response_model=UserDetailsResponse,
    summary="Get user details",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "User not found or Admin UI disabled",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_details(user_id: str, request: Request) -> UserDetailsResponse:
    """Get user details by ID."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID format"}},
            )

        admin_core = api.get_admin()
        if not admin_core:
            logger.error(
                f"Admin module not available for user details (admin {admin_id})"
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

        user = admin_core.get_user_details(uid)
        if not user:
            logger.warning(
                f"Admin {admin_id} requested details for non-existent user {user_id}"
            )
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        logger.debug(f"Admin {admin_id} retrieved details for user {user_id}")

        return UserDetailsResponse(
            id=str(user.id),
            username=user.username,
            email=user.email,
            tier=user.tier or "standard",
            badges=user.badges,
            created_at=user.created_at,
            last_login=user.last_login,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get user details for {user_id} (admin {admin_id}): {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.put(
    "/users/{user_id}/tier",
    response_model=UserTierUpdateResponse,
    summary="Update user tier",
    responses=    {
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "User not found or Admin UI disabled",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_user_tier(
    user_id: str, update: UserTierUpdate, request: Request
) -> UserTierUpdateResponse:
    """Update a user's tier."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        try:
            uid = int(user_id)
        except ValueError:
            logger.warning(
                f"Admin {admin_id} provided invalid user ID for tier update: {user_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        admin_core = api.get_admin()
        if not admin_core:
            logger.error(
                f"Admin module not available for tier update (admin {admin_id})"
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

        success = admin_core.update_user_tier(uid, update.tier)
        if not success:
            logger.warning(
                f"Admin {admin_id} failed to update tier for non-existent user {uid}"
            )
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        logger.info(f"Admin {admin_id} updated user {uid} tier to {update.tier}")

        return UserTierUpdateResponse(success=True, user_id=user_id, tier=update.tier)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to update user {user_id} tier (admin {admin_id}): {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/users/{user_id}/badges/{badge}",
    response_model=UserBadgeUpdateResponse,
    summary="Add user badge",
    responses=    {
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "User not found or Admin UI disabled",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_user_badge(
    user_id: str, badge: str, request: Request
) -> UserBadgeUpdateResponse:
    """Add a badge to a user."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        try:
            uid = int(user_id)
        except ValueError:
            logger.warning(
                f"Admin {admin_id} provided invalid user ID for badge add: {user_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        admin_core = api.get_admin()
        if not admin_core:
            logger.error(f"Admin module not available for badge add (admin {admin_id})")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

        badges = admin_core.add_user_badge(uid, badge)
        if badges is None:
            logger.warning(
                f"Admin {admin_id} failed to add badge '{badge}' to non-existent user {uid}"
            )
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        logger.info(f"Admin {admin_id} added badge '{badge}' to user {uid}")

        return UserBadgeUpdateResponse(success=True, badges=badges)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to add badge '{badge}' to user {user_id} (admin {admin_id}): {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/users/{user_id}/badges/{badge}",
    response_model=UserBadgeUpdateResponse,
    summary="Remove user badge",
    responses=    {
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {
            "model": ErrorResponse,
            "description": "User not found or Admin UI disabled",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_user_badge(
    user_id: str, badge: str, request: Request
) -> UserBadgeUpdateResponse:
    """Remove a badge from a user."""
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        try:
            uid = int(user_id)
        except ValueError:
            logger.warning(
                f"Admin {admin_id} provided invalid user ID for badge removal: {user_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        admin_core = api.get_admin()
        if not admin_core:
            logger.error(
                f"Admin module not available for badge removal (admin {admin_id})"
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

        badges = admin_core.remove_user_badge(uid, badge)
        if badges is None:
            logger.warning(
                f"Admin {admin_id} failed to remove badge '{badge}' from non-existent user {uid}"
            )
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        logger.info(f"Admin {admin_id} removed badge '{badge}' from user {uid}")

        return UserBadgeUpdateResponse(success=True, badges=badges)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to remove badge '{badge}' from user {user_id} (admin {admin_id}): {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/tiers",
    response_model=AvailableTiersResponse,
    summary="Get available tiers",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
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
                AvailableTierInfo(
                    id="alpha", name="Alpha", description="Alpha tester access"
                ),
                AvailableTierInfo(
                    id="beta", name="Beta", description="Beta tester access"
                ),
                AvailableTierInfo(
                    id="premium", name="Premium", description="Premium features"
                ),
                AvailableTierInfo(id="staff", name="Staff", description="Staff member"),
            ]
        )
    except Exception as e:
        logger.error(
            f"Failed to get available tiers (admin {admin_id}): {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/badges",
    response_model=AvailableBadgesResponse,
    summary="Get available badges",
    responses=    {
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
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
            "alpha_tester",
            "early_supporter",
            "staff",
            "verified",
            "bug_hunter",
            "contributor",
            "moderator",
            "partner",
        ]

        logger.debug(
            f"Admin {admin_id} requested available badges, found {len(badges)}"
        )
        return AvailableBadgesResponse(badges=badges)
    except Exception as e:
        logger.error(
            f"Failed to get available badges (admin {admin_id}): {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Database Monitoring Routes ====================


@router.get(
    "/database/pool-health",
    summary="Get database connection pool health",
    responses=    {
        200: {
            "description": "Pool health statistics",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/TelemetryExportResponse"}
                },
                "text/csv": {"schema": {"type": "string"}},
                "text/plain": {"schema": {"type": "string"}},
                "text/html": {"schema": {"type": "string"}},
            },
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_database_pool_health(request: Request) -> Dict[str, Any]:
    """Get database connection pool health and monitoring statistics.
    
    Returns comprehensive pool utilization metrics including:
    - Current connection counts (active, idle, total)
    - Pool configuration (min/max connections)
    - Performance metrics (acquisition time, pool wait time)
    - Connection age tracking and warnings
    - Overall pool health status
    """
    _check_host_restriction(request)
    admin_id = _get_admin_from_token(request)

    try:
        db = api.get_database()
        if not db:
            logger.error(f"Database module not available for pool health check (admin {admin_id})")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not initialized"}},
            )

        stats = db.get_pool_stats()
        
        # Determine health status
        is_healthy = True
        health_issues = []
        
        # Check OS support
        # NOTE: Proxmox containers (LXC) often report 'Linux' via platform.system().
        # We only flag if it's completely unknown or empty.
        import platform
        current_os = platform.system()
        if not current_os:
            is_healthy = False
            health_issues.append("UNSUPPORTED_OS: Unknown operating system")
        
        # Check pool utilization
        active = stats.get("active_connections")
        max_conn = stats.get("max_connections")
        if isinstance(active, (int, float)) and isinstance(max_conn, (int, float)) and max_conn > 0:
            utilization = active / max_conn
            if utilization > 0.9:
                is_healthy = False
                health_issues.append(f"Pool utilization critical: {utilization * 100:.1f}%")
            elif utilization > 0.75:
                health_issues.append(f"Pool utilization high: {utilization * 100:.1f}%")
        
        # Check for old connections
        if stats.get("old_connections"):
            is_healthy = False
            health_issues.append(f"{len(stats['old_connections'])} long-lived connections detected")
        
        # Check pool waits (connection exhaustion events)
        if stats.get("total_pool_waits", 0) > 0:
            is_healthy = False
            health_issues.append(f"Pool exhaustion detected: {stats['total_pool_waits']} wait events")
        
        # Check acquisition time (if it's taking too long)
        avg_acq_time = stats.get("avg_acquisition_time", 0)
        if avg_acq_time > 1.0:  # More than 1 second
            is_healthy = False
            health_issues.append(f"High acquisition time: {avg_acq_time:.3f}s average")
        
        response = {
            **stats,
            "status": "healthy" if is_healthy else "warning" if health_issues else "healthy",
            "health_issues": health_issues,
        }
        
        logger.info(f"Admin {admin_id} retrieved database pool health - Status: {response['status']}")
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get database pool health for admin {admin_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Admin UI Routes ====================


@router.get(
    "/",
    summary="Admin root",
    include_in_schema=False,
)
async def admin_root(request: Request):
    """Redirect to admin login page."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url=request.url_for("admin_login_page"),
        status_code=status.HTTP_302_FOUND
    )


@router.get(
    "/login",
    response_class=HTMLResponse,
    summary="Admin login page",
    responses=    {
        200: {"description": "Admin login page HTML"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
    },
)
async def admin_login_page(request: Request):
    """Serve the admin login page."""
    try:
        _check_host_restriction(request)
        nonce = generate_csp_nonce()
        content = _load_admin_template("login.html", csp_nonce=nonce)
        headers = {"Content-Security-Policy": build_admin_csp_header(nonce)}
        return HTMLResponse(content=content, headers=headers)
    except HTTPException as e:
        if e.status_code == 403:
            return HTMLResponse(
                content="<h1>Access Denied</h1><p>Your IP address is not allowed to access the admin panel.</p>",
                status_code=403
            )
        raise e


@router.get(
    "/ui",
    include_in_schema=False,
)
async def admin_ui_redirect(request: Request):
    """Redirect to admin dashboard page. Frontend JS will handle authentication check."""
    from fastapi.responses import RedirectResponse
    
    return RedirectResponse(
        url=request.url_for("admin_dashboard_page"),
        status_code=status.HTTP_302_FOUND
    )


@router.get(
    "/ui-dashboard",
    summary="Admin dashboard page",
    responses=    {
        200: {"description": "Admin dashboard page HTML"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Access denied (host restriction)",
        },
        404: {"model": ErrorResponse, "description": "Admin UI disabled"},
    },
)
async def admin_dashboard_page(request: Request):
    """Serve the admin dashboard page."""
    try:
        _check_host_restriction(request)
        nonce = generate_csp_nonce()
        content = _load_admin_template("dashboard.html", csp_nonce=nonce)
        headers = {"Content-Security-Policy": build_admin_csp_header(nonce)}
        return HTMLResponse(content=content, headers=headers)
    except HTTPException as e:
        if e.status_code == 403:
            return HTMLResponse(
                content="<h1>Access Denied</h1><p>Your IP address is not allowed to access the admin panel.</p>",
                status_code=403
            )
        raise e