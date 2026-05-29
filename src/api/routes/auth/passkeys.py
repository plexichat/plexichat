from typing import List
from fastapi import APIRouter, Request, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import (
    PasskeyRegisterOptionsRequest,
    PasskeyRegisterOptionsResponse,
    PasskeyRegisterRequest,
    PasskeyResponse,
    PasskeyAuthenticateOptionsRequest,
    PasskeyAuthenticateOptionsResponse,
    PasskeyAuthenticateRequest,
    PasskeyRenameRequest,
    LoginResponse,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from .helpers import _user_to_response
from src.core.auth.exceptions import (
    InvalidCredentialsError,
    AccountLockedError,
)

try:
    from src.core.ratelimit.decorators import rate_limit
except ImportError:
    rate_limit = None

router = APIRouter()


@router.post(
    "/passkeys/options/register",
    response_model=PasskeyRegisterOptionsResponse,
    summary="Get passkey registration options",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Passkey support not available"},
    },
)
@(rate_limit(requests=5, window_seconds=60) if rate_limit else lambda f: f)
async def passkey_register_options(
    body: PasskeyRegisterOptionsRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> PasskeyRegisterOptionsResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        options = auth.generate_passkey_registration_options(
            user_id=current_user.user_id,
            device_name=body.device_name,
        )

        if not options:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Failed to generate registration options",
                    }
                },
            )

        return PasskeyRegisterOptionsResponse(
            challenge_id=options["challenge_id"],
            options=options["options"],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"Passkey registration options failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/passkeys/register",
    response_model=PasskeyResponse,
    summary="Complete passkey registration",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid credential or challenge"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@(rate_limit(requests=3, window_seconds=60) if rate_limit else lambda f: f)
async def passkey_register(
    body: PasskeyRegisterRequest,
    request: Request,
    current_user: TokenInfo = Depends(get_current_user),
) -> PasskeyResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = request.client.host if request.client else None

    try:
        result = auth.verify_passkey_registration(
            user_id=current_user.user_id,
            challenge_id=body.challenge_id,
            credential_response=body.credential,
            ip_address=ip_address,
        )

        if not result:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Registration verification failed",
                    }
                },
            )

        return PasskeyResponse(
            id=result["id"],
            credential_id=result["credential_id"],
            device_name=result["device_name"],
            device_type=result["device_type"],
            created_at=result.get("created_at", 0),
            last_used_at=None,
            backed_up=result["backed_up"],
            revoked=False,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"Passkey registration failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/passkeys/options/authenticate",
    response_model=PasskeyAuthenticateOptionsResponse,
    summary="Get passkey authentication options",
    responses={
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Passkey support not available"},
    },
)
@(rate_limit(requests=10, window_seconds=60) if rate_limit else lambda f: f)
async def passkey_authenticate_options(
    body: PasskeyAuthenticateOptionsRequest,
) -> PasskeyAuthenticateOptionsResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        options = auth.generate_passkey_authentication_options(username=body.username)

        return PasskeyAuthenticateOptionsResponse(
            challenge_id=options["challenge_id"],
            options=options["options"],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"Passkey authentication options failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/passkeys/authenticate",
    response_model=LoginResponse,
    summary="Complete passkey authentication",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@(rate_limit(requests=5, window_seconds=60) if rate_limit else lambda f: f)
async def passkey_authenticate(
    body: PasskeyAuthenticateRequest,
    request: Request,
) -> LoginResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    try:
        result = auth.verify_passkey_authentication(
            challenge_id=body.challenge_id,
            credential_response=body.credential,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return LoginResponse(
            status="success",
            token=result.token,
            user=_user_to_response(result.user) if result.user else None,
        )
    except InvalidCredentialsError as e:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": str(e)}},
        )
    except AccountLockedError as e:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": str(e)}},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"Passkey authentication failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/passkeys",
    response_model=List[PasskeyResponse],
    summary="List user's passkeys",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
@(rate_limit(requests=30, window_seconds=60) if rate_limit else lambda f: f)
async def list_passkeys(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[PasskeyResponse]:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        passkeys = auth.list_passkeys(current_user.user_id)
        return [
            PasskeyResponse(
                id=p["id"],
                credential_id=p["credential_id"],
                device_name=p["device_name"],
                device_type=p["device_type"],
                created_at=p["created_at"],
                last_used_at=p["last_used_at"],
                backed_up=p["backed_up"],
                revoked=p["revoked"],
            )
            for p in passkeys
        ]
    except Exception as e:
        logger.error(f"List passkeys failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/passkeys/{passkey_id}",
    response_model=SuccessResponse,
    summary="Revoke a passkey",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Passkey not found"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
@(rate_limit(requests=10, window_seconds=60) if rate_limit else lambda f: f)
async def revoke_passkey(
    passkey_id: int,
    request: Request,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = request.client.host if request.client else None

    try:
        result = auth.revoke_passkey(
            user_id=current_user.user_id,
            passkey_id=passkey_id,
            ip_address=ip_address,
        )

        if not result:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Passkey not found"}},
            )

        return SuccessResponse(success=True, message=None)
    except Exception as e:
        logger.error(f"Revoke passkey failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/passkeys/{passkey_id}",
    response_model=SuccessResponse,
    summary="Rename a passkey",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Passkey not found"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
@(rate_limit(requests=20, window_seconds=60) if rate_limit else lambda f: f)
async def rename_passkey(
    passkey_id: int,
    body: PasskeyRenameRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        result = auth.rename_passkey(
            user_id=current_user.user_id,
            passkey_id=passkey_id,
            new_name=body.name,
        )

        if not result:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Passkey not found"}},
            )

        return SuccessResponse(success=True, message=None)
    except Exception as e:
        logger.error(f"Rename passkey failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
