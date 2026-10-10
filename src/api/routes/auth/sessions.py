from typing import List
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import Response

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import (
    LoginResponse,
    SessionResponse,
    RevokeAllSessionsRequest,
    RevokeAllSessionsResponse,
    WsTicketResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from .helpers import _user_to_response
from .cookies import (
    clear_session_cookie,
    session_cookie_max_age,
    session_token_for_response,
)
from src.core.auth.exceptions import (
    TokenExpiredError,
    TokenInvalidError,
    UserNotFoundError,
)
from src.utils.net import get_client_ip

router = APIRouter()


@router.post(
    "/logout",
    response_model=SuccessResponse,
    summary="Logout session",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def logout(
    response: Response,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    if current_user.session_id:
        try:
            auth.revoke_session(current_user.user_id, current_user.session_id)
            logger.info(
                f"User {current_user.user_id} logged out session {current_user.session_id}"
            )
        except Exception as e:
            logger.debug(
                f"Failed to revoke session {current_user.session_id} during logout: {e}"
            )

    # Always clear the HttpOnly cookie so the browser forgets the credential.
    clear_session_cookie(response)

    return SuccessResponse(success=True, message=None)


@router.post(
    "/ws-ticket",
    response_model=WsTicketResponse,
    summary="Mint a single-use WebSocket gateway ticket",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        409: {"model": ErrorResponse, "description": "No underlying session"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_ws_ticket(
    current_user: TokenInfo = Depends(get_current_user),
) -> WsTicketResponse:
    """Issue a short-lived, single-use ticket for the gateway handshake.

    Lets the browser authenticate the WebSocket without exposing its
    long-lived bearer token; the ticket is consumed on first use.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    if not current_user.session_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Tickets are only issued for interactive sessions",
                }
            },
        )

    try:
        ticket, expires_in = auth.create_ws_ticket_for_session(
            current_user.user_id, current_user.session_id
        )
    except Exception as e:
        logger.error(f"Failed to mint WS ticket for {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to mint gateway ticket"}},
        )
    return WsTicketResponse(ticket=ticket, expires_in=expires_in)


@router.post(
    "/refresh",
    response_model=LoginResponse,
    summary="Refresh session token",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired session"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def refresh_session(request: Request, response: Response) -> LoginResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    auth_header = request.headers.get("Authorization")
    cookie_token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # Cookie-based sessions carry no Authorization header. Cookies are
        # attached automatically, so apply the same CSRF rule as the auth
        # middleware: mutating requests must carry the client marker header.
        from .cookies import get_session_token_from_cookies

        token = cookie_token = get_session_token_from_cookies(request.cookies)
        if cookie_token and request.headers.get("X-Plexichat-Request") != "1":
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "CSRF validation failed"}},
            )
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Missing authentication token"}},
        )

    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    try:
        # Rotation (F3): mint a fresh token and invalidate the old one.
        # The response re-issues the HttpOnly cookie (F4) so the browser
        # and JS storage stay in sync. M9: the rotated token is echoed in
        # the body SOLELY for explicit non-browser opt-in
        # (``X-Plexichat-Token-Mode: bearer``); cookie flows get only the
        # marker plus the session lifetime, with the credential in the
        # re-issued HttpOnly cookie.
        new_token = auth.refresh_session(token)
        if not new_token:
            raise TokenInvalidError("Invalid or expired session")
        from .cookies import set_session_cookie as _set_cookie

        # Verify the freshly minted token first so the re-issued cookie's
        # Max-Age can track the real session expiry (a trusted device's row
        # outlives the shared expire_hours default).
        token_info = auth.verify_token(new_token, ip_address, user_agent)

        user = auth.get_user(token_info.user_id)
        if not user:
            raise UserNotFoundError("User not found")

        max_age = session_cookie_max_age(getattr(token_info, "expires_at", None))
        _set_cookie(response, new_token, max_age=max_age)

        return LoginResponse(
            status="success",
            token=session_token_for_response(request, new_token),
            user=_user_to_response(user),
            expires_in=max_age,
        )
    except (TokenInvalidError, TokenExpiredError):
        logger.warning("Session refresh failed: invalid or expired token")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid or expired session"}},
        )
    except Exception as e:
        logger.error(f"Unexpected error in refresh_session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/sessions",
    response_model=List[SessionResponse],
    summary="List active sessions",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_sessions_list(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[SessionResponse]:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        sessions = auth.get_sessions(current_user.user_id)
        return [
            SessionResponse(
                id=SnowflakeID(s.id),
                device_id=getattr(s, "device_id", None),
                ip_address=getattr(s, "ip_address", None),
                user_agent=getattr(s, "user_agent", None),
                created_at=getattr(s, "created_at", 0),
                expires_at=getattr(s, "expires_at", 0),
                last_activity=getattr(s, "last_activity", 0),
                current=s.id == current_user.session_id,
            )
            for s in sessions
        ]
    except Exception as e:
        logger.error(
            f"Failed to list sessions for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/sessions/{session_id}",
    response_model=SuccessResponse,
    summary="Revoke session",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid session ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Session not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_session(
    session_id: SnowflakeID, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        auth.revoke_session(current_user.user_id, int(session_id))
        logger.info(f"User {current_user.user_id} revoked session {session_id}")
        return SuccessResponse(success=True, message=None)
    except UserNotFoundError:
        logger.warning(
            f"Session {session_id} not found for user {current_user.user_id}"
        )
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Session not found"}},
        )
    except Exception as e:
        logger.error(
            f"Failed to revoke session {session_id} for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/sessions/revoke-all",
    response_model=RevokeAllSessionsResponse,
    summary="Revoke all sessions",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_all_sessions(
    body: RevokeAllSessionsRequest,
    response: Response,
    current_user: TokenInfo = Depends(get_current_user),
) -> RevokeAllSessionsResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    except_current = body.except_current

    try:
        sessions = auth.get_sessions(current_user.user_id)
        revoked = 0
        for s in sessions:
            if except_current and s.id == current_user.session_id:
                continue
            try:
                auth.revoke_session(current_user.user_id, s.id)
                revoked += 1
            except Exception as e:
                logger.debug(
                    f"Failed to revoke session {s.id} for user {current_user.user_id}: {e}"
                )

        logger.info(
            f"User {current_user.user_id} revoked {revoked} sessions (except_current={except_current})"
        )
        if not except_current or not current_user.session_id:
            # The current session was revoked — drop the browser cookie too.
            clear_session_cookie(response)
        return RevokeAllSessionsResponse(success=True, revoked_count=revoked)
    except Exception as e:
        logger.error(
            f"Failed to revoke all sessions for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
