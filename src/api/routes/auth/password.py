from fastapi import APIRouter, HTTPException

import src.api as api
import utils.logger as logger
from src.api.schemas.auth import (
    PasswordRequirementsResponse,
    PasswordResetRequest,
    PasswordResetConfirm,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.auth.exceptions import (
    TokenInvalidError,
    WeakPasswordError,
)

try:
    import utils.config as config_util
except ImportError:
    config_util = None

router = APIRouter()


@router.get(
    "/password-requirements",
    response_model=PasswordRequirementsResponse,
    summary="Get password requirements",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_password_requirements() -> PasswordRequirementsResponse:
    try:
        auth_config = {}
        if config_util:
            auth_config = config_util.get("authentication", {})
            if not isinstance(auth_config, dict):
                auth_config = {}

        password_config = auth_config.get("password", {})
        if not isinstance(password_config, dict):
            password_config = {}

        accounts_config = auth_config.get("accounts", {})
        if not isinstance(accounts_config, dict):
            accounts_config = {}

        from src.api.routes.docs import is_docs_enabled

        return PasswordRequirementsResponse(
            min_length=password_config.get("min_length", 12),
            max_length=password_config.get("max_length", 128),
            require_uppercase=password_config.get("require_uppercase", True),
            require_lowercase=password_config.get("require_lowercase", True),
            require_digit=password_config.get("require_digit", True),
            require_special=password_config.get("require_special", True),
            age_gate_enabled=accounts_config.get("age_gate_enabled", False),
            age_verification_type=accounts_config.get(
                "age_verification_type", "boolean"
            ),
            minimum_age=accounts_config.get("minimum_age", 13),
            docs_enabled=is_docs_enabled(),
        )
    except Exception as e:
        logger.error(f"Failed to get password requirements: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/password-reset/request",
    response_model=SuccessResponse,
    summary="Request password reset",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def request_password_reset(body: PasswordResetRequest) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        from fastapi.concurrency import run_in_threadpool

        def _request_reset_with_cleanup(email_str):
            db = api.get_db()
            try:
                return auth.request_password_reset(email_str)
            finally:
                if db:
                    db.close()

        await run_in_threadpool(_request_reset_with_cleanup, body.email)
        return SuccessResponse(success=True, message=None)
    except Exception as e:
        logger.error(f"Password reset request failed: {e}", exc_info=True)
        return SuccessResponse(success=True, message=None)


@router.post(
    "/password-reset/confirm",
    response_model=SuccessResponse,
    summary="Confirm password reset",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid token or weak password"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def confirm_password_reset(body: PasswordResetConfirm) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        from fastapi.concurrency import run_in_threadpool

        def _reset_with_cleanup(token_str, new_password):
            db = api.get_db()
            try:
                return auth.reset_password(token_str, new_password)
            finally:
                if db:
                    db.close()

        success = await run_in_threadpool(
            _reset_with_cleanup, body.token, body.new_password
        )
        if success:
            return SuccessResponse(success=True, message=None)
        else:
            raise TokenInvalidError("Invalid or expired token")
    except TokenInvalidError as e:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": str(e)}},
        )
    except WeakPasswordError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"Password reset confirmation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
