from typing import List
from fastapi import APIRouter, Request, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import (
    LoginResponse,
    SessionResponse,
    RevokeAllSessionsRequest,
    RevokeAllSessionsResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from .helpers import _user_to_response
from src.core.auth.exceptions import (
    TokenExpiredError,
    TokenInvalidError,
    UserNotFoundError,
)

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

    return SuccessResponse(success=True, message=None)


@router.post(
    "/refresh",
    response_model=LoginResponse,
    summary="Refresh session token",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired session"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def refresh_session(request: Request) -> LoginResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Missing authentication token"}},
        )

    token = auth_header[7:]
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    try:
        token_info = auth.verify_token(token, ip_address, user_agent)

        user = auth.get_user(token_info.user_id)
        if not user:
            raise UserNotFoundError("User not found")

        return LoginResponse(
            status="success",
            token=token,
            user=_user_to_response(user),
        )
    except (TokenInvalidError, TokenExpiredError) as e:
        logger.warning(f"Session refresh failed: {e}")
        raise HTTPException(
            status_code=401, detail={"error": {"code": 401, "message": str(e)}}
        )
    except Exception as e:
        logger.error(f"Unexpected error in refresh_session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
    session_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        sid = int(session_id)
    except ValueError:
        logger.warning(f"Invalid session ID format: {session_id}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid session ID"}},
        )

    try:
        auth.revoke_session(current_user.user_id, sid)
        logger.info(f"User {current_user.user_id} revoked session {sid}")
        return SuccessResponse(success=True, message=None)
    except UserNotFoundError:
        logger.warning(f"Session {sid} not found for user {current_user.user_id}")
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
    body: RevokeAllSessionsRequest, current_user: TokenInfo = Depends(get_current_user)
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
        return RevokeAllSessionsResponse(success=True, revoked_count=revoked)
    except Exception as e:
        logger.error(
            f"Failed to revoke all sessions for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
