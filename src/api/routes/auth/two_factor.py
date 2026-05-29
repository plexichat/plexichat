from fastapi import APIRouter, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import (
    TwoFactorRequest,
    LoginResponse,
    TwoFactorStatusResponse,
    TwoFactorSetupRequest,
    TwoFactorSetupResponse,
    TwoFactorConfirmRequest,
    TwoFactorDisableRequest,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from .helpers import _user_to_response
from src.core.auth.exceptions import (
    AuthError,
    InvalidCredentialsError,
    AccountLockedError,
    AccountDisabledError,
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorInvalidError,
    TwoFactorRequiredError,
    TwoFactorSetupError,
    UserNotFoundError,
)

router = APIRouter()


@router.post(
    "/2fa",
    response_model=LoginResponse,
    summary="Complete 2FA",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired 2FA code"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def complete_2fa(body: TwoFactorRequest) -> LoginResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        result = auth.complete_2fa(body.challenge_token, body.code)
    except (TokenInvalidError, TokenExpiredError) as e:
        logger.warning(f"2FA completion failed: {e}")
        raise HTTPException(
            status_code=401, detail={"error": {"code": 401, "message": str(e)}}
        )
    except TwoFactorInvalidError:
        logger.warning("2FA completion failed: Invalid code")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid 2FA code"}},
        )
    except UserNotFoundError:
        logger.warning("2FA completion failed: User not found")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid challenge"}},
        )
    except Exception as e:
        logger.error(f"Unexpected error in complete_2fa: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )

    logger.info(
        f"2FA completed successfully for user ID: {getattr(result.user, 'id', 'unknown')}"
    )
    return LoginResponse(
        status="success",
        token=result.token,
        user=_user_to_response(result.user) if result.user else None,
        challenge_token=None,
        methods=None,
        expires_in=None,
    )


@router.get(
    "/2fa/status",
    response_model=TwoFactorStatusResponse,
    summary="Get 2FA status",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_2fa_status(
    current_user: TokenInfo = Depends(get_current_user),
) -> TwoFactorStatusResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        status = auth.get_2fa_status(current_user.user_id)
        return TwoFactorStatusResponse(
            enabled=status.enabled, backup_codes_remaining=status.backup_codes_remaining
        )
    except UserNotFoundError:
        logger.warning(
            f"2FA status check failed: User {current_user.user_id} not found"
        )
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "User not found"}},
        )
    except Exception as e:
        logger.error(
            f"Failed to get 2FA status for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/2fa/enable",
    response_model=TwoFactorSetupResponse,
    summary="Enable 2FA",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Password required or invalid data",
        },
        401: {"model": ErrorResponse, "description": "Invalid password"},
        404: {"model": ErrorResponse, "description": "User not found"},
        409: {"model": ErrorResponse, "description": "2FA is already enabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def enable_2fa(
    body: TwoFactorSetupRequest, current_user: TokenInfo = Depends(get_current_user)
) -> TwoFactorSetupResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    password = body.password
    if not password:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Password required"}},
        )

    try:
        user = auth.get_user(current_user.user_id)
        if not user:
            logger.warning(f"User {current_user.user_id} not found during 2FA enable")
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        if getattr(user, "totp_enabled", False):
            logger.warning(
                f"User {current_user.user_id} attempted to enable 2FA but it is already enabled"
            )
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": 409, "message": "2FA is already enabled"}},
            )

        try:
            auth.login(user.username, password)
        except InvalidCredentialsError:
            logger.warning(
                f"2FA enable failed for user {current_user.user_id}: Invalid password"
            )
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Invalid password"}},
            )
        except TwoFactorRequiredError:
            pass
        except (AccountLockedError, AccountDisabledError) as e:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": str(e)}},
            )

        result = auth.setup_2fa(current_user.user_id)
        logger.info(f"2FA setup initiated for user {current_user.user_id}")
        return TwoFactorSetupResponse(
            secret=result.secret,
            qr_uri=result.qr_uri,
            backup_codes=result.backup_codes or [],
        )
    except HTTPException:
        raise
    except AuthError as e:
        if "already" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": 409, "message": "2FA is already enabled"}},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(
            f"Failed to enable 2FA for user {current_user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/2fa/confirm",
    response_model=SuccessResponse,
    summary="Confirm 2FA setup",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Valid 6-digit code required or 2FA setup not started",
        },
        401: {"model": ErrorResponse, "description": "Invalid code"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def confirm_2fa_setup(
    body: TwoFactorConfirmRequest, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    code = body.code
    if not code or len(code) != 6:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Valid 6-digit code required"}},
        )

    try:
        success = auth.confirm_2fa(current_user.user_id, code)
    except TwoFactorInvalidError:
        logger.warning(
            f"2FA confirm failed for user {current_user.user_id}: Invalid code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid code"}},
        )
    except (UserNotFoundError, TwoFactorSetupError, AuthError) as e:
        logger.warning(f"2FA confirm failed for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in confirm_2fa_setup for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )

    if success:
        logger.info(f"2FA confirmed and enabled for user {current_user.user_id}")
        return SuccessResponse(success=True, message=None)
    else:
        logger.warning(
            f"2FA confirm failed for user {current_user.user_id}: Invalid code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid code"}},
        )


@router.post(
    "/2fa/disable",
    response_model=SuccessResponse,
    summary="Disable 2FA",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Password or code required or 2FA not enabled",
        },
        401: {"model": ErrorResponse, "description": "Invalid password or code"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def disable_2fa(
    body: TwoFactorDisableRequest, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    password = body.password
    code = body.code

    if not password:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Password required"}},
        )
    if not code:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "2FA code required"}},
        )

    try:
        auth.disable_2fa(current_user.user_id, password, code)
        logger.info(f"2FA disabled for user {current_user.user_id}")
        return SuccessResponse(success=True, message=None)
    except InvalidCredentialsError:
        logger.warning(
            f"2FA disable failed for user {current_user.user_id}: Invalid password"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid password"}},
        )
    except TwoFactorInvalidError:
        logger.warning(
            f"2FA disable failed for user {current_user.user_id}: Invalid 2FA code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid 2FA code"}},
        )
    except AuthError as e:
        if "not enabled" in str(e).lower():
            logger.warning(
                f"2FA disable failed for user {current_user.user_id}: 2FA not enabled"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "2FA is not enabled"}},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in disable_2fa for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
