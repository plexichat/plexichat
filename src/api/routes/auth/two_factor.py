from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import Response

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import (
    LoginResponse,
    TwoFactorRequest,
    TwoFactorStatusResponse,
    TwoFactorSetupRequest,
    TwoFactorSetupResponse,
    TwoFactorConfirmRequest,
    TwoFactorDisableRequest,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.api.routes.users.helpers import require_user_token
from .helpers import (
    _user_to_response,
    MAX_2FA_CODE_LENGTH,
    MAX_CREDENTIAL_TOKEN_LENGTH,
    validate_credential_input,
)
from .cookies import (
    set_session_cookie,
    session_expiry_seconds,
    session_token_for_response,
)
from src.core.auth.exceptions import (
    AuthError,
    InvalidCredentialsError,
    AccountLockedError,
    AccountDisabledError,
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorInvalidError,
    TwoFactorSetupError,
    UserNotFoundError,
    EmailNotVerifiedError,
)
from src.core.auth.models import AuthStatus
from src.utils.net import get_client_ip

try:
    from src.core.ratelimit.decorators import rate_limit
except ImportError as _2fa_rl_exc:  # pragma: no cover
    rate_limit = None
    import utils.logger as _rl_logger
    import os as _rl_os
    import utils.config as _rl_cfg

    _rl_logger.critical(
        "2FA RATE_LIMIT IMPORT FAILED: %s. "
        "2FA routes will run WITHOUT rate limiting. "
        "Install plexichat-ratelimit and restart, OR explicitly "
        "opt-out by setting PLEXICHAT_ALLOW_2FA_NO_RATELIMIT=1.",
        _2fa_rl_exc,
    )
    try:
        _rl_required = bool(_rl_cfg.get("rate_limiting", {}).get("required", False))
    except Exception:
        _rl_required = False
    if _rl_required or _rl_os.environ.get("PLEXICHAT_REQUIRE_FAIL_CLOSED", "") not in (
        "",
        "0",
    ):
        if _rl_os.environ.get("PLEXICHAT_ALLOW_2FA_NO_RATELIMIT", "") in (
            "1",
            "true",
            "yes",
        ):
            _rl_logger.critical("Opted-in to running without 2FA rate-limit.")
        else:
            raise RuntimeError(
                "2FA rate-limit import failed and fail-closed mode is required; refusing to start."
            ) from _2fa_rl_exc

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
@(rate_limit(requests=5, window_seconds=60) if rate_limit else lambda f: f)
async def complete_2fa(
    request: Request, response: Response, body: TwoFactorRequest
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

    # Reject malformed credentials at the trust boundary before the auth core
    # hashes the challenge secret or runs TOTP/backup-code verification on the
    # supplied value. Control characters and oversized bodies have no place in
    # a token/code and would otherwise force needless work or a deep 500.
    try:
        challenge_token = validate_credential_input(
            body.challenge_token, "challenge_token", MAX_CREDENTIAL_TOKEN_LENGTH
        )
        code = validate_credential_input(body.code, "code", MAX_2FA_CODE_LENGTH)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )

    try:
        result = auth.complete_2fa(challenge_token, code, ip_address, user_agent)
    except (TokenInvalidError, TokenExpiredError):
        logger.warning("2FA completion failed: invalid or expired challenge")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid or expired challenge"}},
        )
    except TwoFactorInvalidError as e:
        # Includes per-user limiter "Too many attempts" and per-challenge limit
        msg = str(e)
        if "Too many attempts" in msg:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "code": 429,
                        "message": "Too many attempts; try again later",
                    }
                },
            )
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
    except (AccountLockedError, EmailNotVerifiedError):
        # Account state changed between challenge mint and completion. Report
        # the same generic failure as an invalid challenge so locked/unverified
        # accounts stay indistinguishable from a bogus credential.
        logger.warning("2FA completion refused: account state no longer valid")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid or expired challenge"}},
        )
    except Exception as e:
        logger.error(f"Unexpected error in complete_2fa: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    logger.info(
        f"2FA completed successfully for user ID: {getattr(result.user, 'id', 'unknown')}"
    )
    # Fail closed (C1): the verify step must only ever emit success when the
    # core actually returned SUCCESS with a session token. Without this
    # guard a future non-success AuthResult would be rendered as a
    # "success" body with a null credential.
    if result.status != AuthStatus.SUCCESS or not result.token:
        logger.error(f"Unexpected auth status in 2FA completion: {result.status!r}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    # A challenge is created only after the login route has cleared the account
    # state checks, but it stays valid for minutes. The core 2FA verifier does
    # not re-check account state, so a challenge minted for a healthy account
    # can otherwise still be exchanged for a fresh session after that account
    # has been locked or scheduled for deletion. Fail closed and revoke the
    # just-created session so no usable credential survives the refusal.
    try:
        current_user = auth.get_user(result.user.id) if result.user else None
    except Exception as e:
        logger.error(f"Failed to re-check account state after 2FA: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    if current_user is None or (
        getattr(current_user, "account_locked", False)
        or getattr(current_user, "deletion_status", "active") == "frozen"
    ):
        session_id = getattr(result.session, "id", None)
        if session_id is not None:
            try:
                auth.revoke_session(result.user.id, int(session_id))
            except Exception as e:
                logger.debug(f"Failed to revoke session after blocked 2FA: {e}")
        logger.warning("2FA completion refused: account is no longer active")
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid or expired challenge"}},
        )

    # M9 (same as login): the full session goes to browsers ONLY via the
    # HttpOnly cookie; the body carries a marker plus the session
    # lifetime, with the raw token echoed back solely for explicit
    # non-browser opt-in (``X-Plexichat-Token-Mode: bearer``).
    # Mirror the real session expiry (trusted-device rows are longer-lived).
    max_age = session_expiry_seconds(result.session)
    set_session_cookie(response, result.token, max_age=max_age)
    return LoginResponse(
        status=AuthStatus.SUCCESS.value,
        token=session_token_for_response(request, result.token),
        user=_user_to_response(result.user) if result.user else None,
        challenge_token=None,
        methods=None,
        expires_in=max_age,
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
    require_user_token(current_user)
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
        429: {"model": ErrorResponse, "description": "Too many requests"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
# Password-verification route: without a limiter an authenticated session
# could brute-force the account password through this endpoint (Agent 50 F2
# parity — the other 2FA verify routes carry the same 5/60s budget).
@(rate_limit(requests=5, window_seconds=60) if rate_limit else lambda f: f)
async def enable_2fa(
    body: TwoFactorSetupRequest, current_user: TokenInfo = Depends(get_current_user)
) -> TwoFactorSetupResponse:
    require_user_token(current_user)
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
            # Re-confirm the password without minting a throwaway session;
            # auth.login() would create an orphaned session row here.
            if not auth.verify_user_password(current_user.user_id, password):
                logger.warning(
                    f"2FA enable failed for user {current_user.user_id}: Invalid password"
                )
                raise HTTPException(
                    status_code=401,
                    detail={"error": {"code": 401, "message": "Invalid password"}},
                )
        except HTTPException:
            raise
        except (AccountLockedError, AccountDisabledError):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {"code": 403, "message": "Account locked or disabled"}
                },
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
    except AuthError:
        # Already-enabled is handled above; generic auth error
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(
            f"Failed to enable 2FA for user {current_user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
@(rate_limit(requests=5, window_seconds=60) if rate_limit else lambda f: f)
async def confirm_2fa_setup(
    body: TwoFactorConfirmRequest,
    response: Response = None,  # type: ignore[assignment]  # injected by FastAPI
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    require_user_token(current_user)
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
        success = auth.confirm_2fa(
            current_user.user_id,
            code,
            except_session_id=current_user.session_id,
        )
    except TwoFactorInvalidError as e:
        msg = str(e)
        if "Too many attempts" in msg:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "code": 429,
                        "message": "Too many attempts; try again later",
                    }
                },
            )
        logger.warning(
            f"2FA confirm failed for user {current_user.user_id}: Invalid code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid code"}},
        )
    except (UserNotFoundError, TwoFactorSetupError, AuthError):
        logger.warning(
            f"2FA confirm failed for user {current_user.user_id}: invalid setup"
        )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in confirm_2fa_setup for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    if success:
        logger.info(f"2FA confirmed and enabled for user {current_user.user_id}")
        # Privilege transition (password-only -> 2FA): rotate the session id.
        # The session that just proved possession of the TOTP code was minted
        # BEFORE 2FA existed, so it may be a bearer an attacker already knows;
        # keeping that id alive would let a fixated/stolen pre-2FA token ride
        # the newly enabled second factor. Revoke it and hand the legitimate
        # user a brand-new session (new id + secret) via a fresh Set-Cookie.
        rotated = auth.rotate_session_id(current_user.user_id, current_user.session_id)
        if response is not None:
            set_session_cookie(response, rotated.token, session_expiry_seconds(rotated))
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
@(rate_limit(requests=5, window_seconds=60) if rate_limit else lambda f: f)
async def disable_2fa(
    body: TwoFactorDisableRequest,
    response: Response = None,  # type: ignore[assignment]  # injected by FastAPI
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    require_user_token(current_user)
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
        auth.disable_2fa(
            current_user.user_id,
            password,
            code,
            except_session_id=current_user.session_id,
        )
        logger.info(f"2FA disabled for user {current_user.user_id}")
        # Disabling 2FA is also a privilege transition: the preserved current
        # session was minted under the old (2FA) policy and must not keep its id
        # across the switch. Rotate it and re-issue the HttpOnly cookie.
        rotated = auth.rotate_session_id(current_user.user_id, current_user.session_id)
        if response is not None:
            set_session_cookie(response, rotated.token, session_expiry_seconds(rotated))
        return SuccessResponse(success=True, message=None)
    except InvalidCredentialsError:
        logger.warning(
            f"2FA disable failed for user {current_user.user_id}: Invalid password"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid password"}},
        )
    except TwoFactorInvalidError as e:
        msg = str(e)
        if "Too many attempts" in msg:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "code": 429,
                        "message": "Too many attempts; try again later",
                    }
                },
            )
        logger.warning(
            f"2FA disable failed for user {current_user.user_id}: Invalid 2FA code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid 2FA code"}},
        )
    except AuthError:
        logger.warning(
            f"2FA disable failed for user {current_user.user_id}: 2FA not enabled"
        )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "2FA is not enabled"}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in disable_2fa for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
