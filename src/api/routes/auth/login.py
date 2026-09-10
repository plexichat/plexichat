from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

import src.api as api
import utils.logger as logger
from utils.logger import mask_string
from src.api.schemas.auth import LoginRequest, LoginResponse
from src.api.schemas.common import ErrorResponse
from .helpers import _user_to_response
from .cookies import (
    set_session_cookie,
    session_cookie_max_age,
    session_token_for_response,
)
from src.core.auth.exceptions import (
    InvalidCredentialsError,
    AccountLockedError,
    AccountDisabledError,
    EmailNotVerifiedError,
)
from src.core.auth.models import AuthStatus
from src.utils.net import get_client_ip

router = APIRouter()


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="User login",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def login(
    request: Request, response: Response, body: LoginRequest
) -> LoginResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    # Trusted-device support: the client may send a stable fingerprint so the
    # server can recognize a previously trusted device and skip the 2FA
    # challenge. Server-side trust only — no client claim is honored by itself.
    device_info = None
    if body.device_fingerprint:
        device_info = {"fingerprint": body.device_fingerprint}

    try:
        result = auth.login(
            username=body.username,
            password=body.password,
            device_info=device_info,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except InvalidCredentialsError:
        masked_username = mask_string(body.username)
        logger.warning(f"Login failed for '{masked_username}': Invalid credentials")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid credentials"}},
        )
    # Account-state enumeration guard (M3): locked/disabled/unverified must
    # be indistinguishable from bad credentials, otherwise attackers can
    # probe which usernames exist and their state via status/message deltas.
    except (AccountLockedError, EmailNotVerifiedError, AccountDisabledError) as e:
        masked_username = mask_string(body.username)
        logger.warning(f"Login failed for '{masked_username}': {type(e).__name__}")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid credentials"}},
        )
    except Exception as e:
        masked_username = mask_string(body.username)
        logger.error(
            f"Unexpected error in login for '{masked_username}': {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Internal server error during login"}
            },
        )

    if result.status == AuthStatus.TWO_FACTOR_REQUIRED:
        masked_username = mask_string(body.username)
        logger.info(f"2FA challenge issued for user '{masked_username}'")
        return LoginResponse(
            status="two_factor_required",
            token=None,
            user=None,
            challenge_token=result.challenge_token,
            methods=result.methods,
            expires_in=result.expires_in,
        )

    masked_username = mask_string(body.username)
    logger.info(
        f"User '{masked_username}' logged in successfully (ID: {getattr(result.user, 'id', 'unknown')})"
    )
    # M9: the session travels to browsers ONLY as an HttpOnly cookie so an
    # XSS payload cannot exfiltrate it from the JSON body. The body carries
    # just a status marker plus the session lifetime; the raw token is
    # echoed back SOLELY for non-browser clients that explicitly opt in
    # with ``X-Plexichat-Token-Mode: bearer``. Browser WebSocket auth uses
    # short-lived single-use wst.* tickets minted via /auth/ws-ticket.
    set_session_cookie(response, result.token)
    return LoginResponse(
        status="success",
        token=session_token_for_response(request, result.token),
        user=_user_to_response(result.user) if result.user else None,
        challenge_token=None,
        methods=None,
        expires_in=session_cookie_max_age(),
    )
