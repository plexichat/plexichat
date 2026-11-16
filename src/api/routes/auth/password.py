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

# The password policy is static configuration. Building the response used to
# re-read three config keys on every request (this endpoint is public and
# frequently polled by the client at startup). Cache the serialized response
# and rebuild it only when the underlying config dicts actually change, so a
# runtime config.set is still honoured.
_password_policy_cache = None
_password_policy_cache_key = None


def _policy_config() -> tuple:
    auth_config = config_util.get("authentication", {}) if config_util else {}
    if not isinstance(auth_config, dict):
        auth_config = {}

    password_config = auth_config.get("password", {})
    if not isinstance(password_config, dict):
        password_config = {}

    accounts_config = auth_config.get("accounts", {})
    if not isinstance(accounts_config, dict):
        accounts_config = {}

    return password_config, accounts_config


def _build_password_requirements(password_config: dict, accounts_config: dict):
    from src.api.routes.docs import is_docs_enabled

    return PasswordRequirementsResponse(
        min_length=password_config.get("min_length", 12),
        max_length=password_config.get("max_length", 128),
        require_uppercase=password_config.get("require_uppercase", True),
        require_lowercase=password_config.get("require_lowercase", True),
        require_digit=password_config.get("require_digit", True),
        require_special=password_config.get("require_special", True),
        age_gate_enabled=accounts_config.get("age_gate_enabled", False),
        age_verification_type=accounts_config.get("age_verification_type", "boolean"),
        minimum_age=accounts_config.get("minimum_age", 13),
        docs_enabled=is_docs_enabled(),
    )


def invalidate_password_policy_cache() -> None:
    """Force a rebuild on the next request (tests / runtime config reload)."""
    global _password_policy_cache, _password_policy_cache_key
    _password_policy_cache = None
    _password_policy_cache_key = None


@router.get(
    "/password-requirements",
    response_model=PasswordRequirementsResponse,
    summary="Get password requirements",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_password_requirements() -> PasswordRequirementsResponse:
    global _password_policy_cache, _password_policy_cache_key
    try:
        password_config, accounts_config = _policy_config()
        # repr() yields a hashable fingerprint that changes whether the config
        # dicts are replaced or mutated in place.
        cache_key = (repr(password_config), repr(accounts_config))
        cached = _password_policy_cache
        if cached is not None and cache_key == _password_policy_cache_key:
            return cached

        response = _build_password_requirements(password_config, accounts_config)
        _password_policy_cache = response
        _password_policy_cache_key = cache_key
        return response
    except Exception as e:
        logger.error(f"Failed to get password requirements: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
    except TokenInvalidError:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Authentication required"}},
        )
    except WeakPasswordError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(f"Password reset confirmation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
