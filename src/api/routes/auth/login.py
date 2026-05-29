from fastapi import APIRouter, Request, HTTPException

import src.api as api
import utils.logger as logger
from utils.logger import mask_string
from src.api.schemas.auth import LoginRequest, LoginResponse
from src.api.schemas.common import ErrorResponse
from .helpers import _user_to_response
from src.core.auth.exceptions import (
    InvalidCredentialsError,
    AccountLockedError,
    AccountDisabledError,
    EmailNotVerifiedError,
)

router = APIRouter()


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="User login",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        403: {
            "model": ErrorResponse,
            "description": "Account locked or email not verified",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def login(request: Request, body: LoginRequest) -> LoginResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    try:
        result = auth.login(
            username=body.username,
            password=body.password,
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
    except AccountLockedError:
        masked_username = mask_string(body.username)
        logger.warning(f"Login failed for '{masked_username}': Account locked")
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Account locked"}},
        )
    except (EmailNotVerifiedError, AccountDisabledError) as e:
        masked_username = mask_string(body.username)
        logger.warning(f"Login failed for '{masked_username}': {type(e).__name__}")
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
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

    if result.status.value == "two_factor_required":
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
    return LoginResponse(
        status="success",
        token=result.token,
        user=_user_to_response(result.user) if result.user else None,
        challenge_token=None,
        methods=None,
        expires_in=None,
    )
