from fastapi import APIRouter, Request, HTTPException

import src.api as api
import utils.logger as logger
from utils.logger import mask_email, mask_string
from src.api.schemas.auth import RegisterRequest, LoginResponse
from src.api.schemas.common import ErrorResponse
from .helpers import _user_to_response
from src.core.auth.exceptions import (
    AuthError,
    UserExistsError,
    InvalidUsernameError,
    InvalidEmailError,
    WeakPasswordError,
)

try:
    import utils.config as config_util
except ImportError:
    config_util = None

router = APIRouter()


@router.post(
    "/register",
    response_model=LoginResponse,
    summary="Register a new user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid registration data"},
        401: {
            "model": ErrorResponse,
            "description": "Invalid credentials or session expired",
        },
        409: {"model": ErrorResponse, "description": "User already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def register(request: Request, body: RegisterRequest) -> LoginResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = request.client.host if request.client else None
    device_info = {"user_agent": request.headers.get("User-Agent", "unknown")}

    age = body.age
    if age is None and body.age_verified is True:
        min_age = 13
        if config_util:
            min_age = config_util.get("authentication.accounts.minimum_age", 13)
        age = min_age

    is_internal = getattr(request.state, "is_internal", False)

    try:
        user = auth.register(
            username=body.username,
            email=body.email,
            password=body.password,
            device_info=device_info,
            ip_address=ip_address,
            age=age,
            dob=body.dob,
            is_internal=is_internal,
        )
    except UserExistsError:
        masked_username = mask_string(body.username)
        masked_email_addr = mask_email(body.email)
        logger.warning(
            f"Registration failed: User '{masked_username}' or email '{masked_email_addr}' already exists"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Username or email already exists",
                }
            },
        )
    except (InvalidUsernameError, InvalidEmailError, WeakPasswordError, AuthError) as e:
        masked_username = mask_string(body.username)
        logger.warning(f"Registration failed for '{masked_username}': {e}")

        error_message = str(e)
        if isinstance(e, WeakPasswordError) and config_util:
            guidance_url = (
                config_util.get("authentication", {})
                .get("password", {})
                .get("guidance_url")
            )
            if guidance_url:
                error_message += f" For password guidance, see: {guidance_url}"

        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": error_message}}
        )
    except Exception as e:
        masked_username = mask_string(body.username)
        logger.error(
            f"Unexpected error in register for '{masked_username}': {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": 500,
                    "message": "Internal server error during registration",
                }
            },
        )

    features = api.get_features()
    if features:
        try:
            features.apply_new_user_features(user.id)
        except Exception as fe:
            logger.debug(f"Failed to apply new user features for user {user.id}: {fe}")

    try:
        result = auth.create_session_for_user(
            user_id=user.id,
            device_info=device_info,
            ip_address=ip_address,
            user_agent=request.headers.get("User-Agent"),
        )
    except Exception as le:
        logger.error(f"Auto-login failed after registration for user {user.id}: {le}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": 401,
                    "message": "Auto-login failed after registration",
                }
            },
        )

    return LoginResponse(
        status="success", token=result.token, user=_user_to_response(user)
    )
