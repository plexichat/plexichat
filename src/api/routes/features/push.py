from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.security.hardening import HardeningViolation, reject_control_characters
from src.api.routes.users.helpers import require_user_token
from .common import raise_bad_request
from src.core.push.exceptions import PermissionDeniedError

router = APIRouter()


MAX_TOKEN_LENGTH = 2048
MAX_DEVICE_ID_LENGTH = 256
MAX_APP_VERSION_LENGTH = 64
MAX_P256DH_LENGTH = 256
MAX_AUTH_LENGTH = 128
VALID_PLATFORMS = frozenset({"ios", "android", "web"})


def _bounded_token_text(value, field: str, maximum: int):
    if value is None:
        return value
    candidate = str(value)
    if not candidate or len(candidate) > maximum:
        raise ValueError(f"Invalid {field}")
    try:
        return reject_control_characters(candidate, field)
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc


class PushTokenRequest(BaseModel):
    token: str = Field(
        ..., min_length=1, max_length=MAX_TOKEN_LENGTH, description="Push token"
    )
    platform: str = Field(..., description="Platform: ios, android, or web")
    device_id: Optional[str] = Field(
        None, max_length=MAX_DEVICE_ID_LENGTH, description="Device identifier"
    )
    app_version: Optional[str] = Field(
        None, max_length=MAX_APP_VERSION_LENGTH, description="App version string"
    )
    p256dh: Optional[str] = Field(
        None, max_length=MAX_P256DH_LENGTH, description="Web Push P-256 public key"
    )
    auth: Optional[str] = Field(
        None, max_length=MAX_AUTH_LENGTH, description="Web Push auth secret"
    )

    @field_validator("token", "device_id", "app_version", "p256dh", "auth")
    @classmethod
    def _safe_text(cls, value, info):
        limits = {
            "token": MAX_TOKEN_LENGTH,
            "device_id": MAX_DEVICE_ID_LENGTH,
            "app_version": MAX_APP_VERSION_LENGTH,
            "p256dh": MAX_P256DH_LENGTH,
            "auth": MAX_AUTH_LENGTH,
        }
        return _bounded_token_text(value, info.field_name, limits[info.field_name])

    @field_validator("platform")
    @classmethod
    def _safe_platform(cls, value: str, info):
        if value not in VALID_PLATFORMS:
            raise ValueError("Invalid platform")
        return value


@router.post(
    "/push/tokens",
    summary="Register push notification token",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def register_push_token(
    body: PushTokenRequest, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    if body.platform not in VALID_PLATFORMS:
        raise_bad_request("Invalid platform")

    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)

    try:
        result = svc.register_token(
            user_id=current_user.user_id,
            token=body.token,
            platform=body.platform,
            device_id=body.device_id,
            app_version=body.app_version,
            p256dh=body.p256dh,
            auth=body.auth,
        )
        # SECURITY: never echo the raw token / Web Push keys back.
        # The row is opaque to the OpenAPI walk, so stringify its snowflake
        # ids here: ``id`` is a generated snowflake and ``user_id`` the
        # owner's, and a JS Number would round both past 2**53.
        return stringify_snowflake_ids(
            {
                "success": True,
                "token_record": PushManager._sanitize_token_record(result),
            }
        )
    except ValueError:
        raise_bad_request("Invalid push token data")
    except PermissionDeniedError:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Push token operation denied"}},
        )


@router.get(
    "/push/tokens",
    summary="List registered push tokens",
    responses={401: {"model": ErrorResponse}},
)
async def list_push_tokens(
    current_user: TokenInfo = Depends(get_current_user),
):
    require_user_token(current_user)
    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)
    tokens = svc.get_user_tokens(current_user.user_id)
    # SECURITY: get_user_tokens returns decrypted credentials for delivery
    # use only; strip them before serialising to the client. The opaque dict
    # also bypasses the OpenAPI id walk, so stringify the snowflake ids here.
    return stringify_snowflake_ids(
        {"tokens": [PushManager._sanitize_token_record(t) for t in tokens]}
    )


@router.delete(
    "/push/tokens/id/{token_id}",
    summary="Unregister push token by id",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def unregister_push_token_by_id(
    token_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    # Preferred removal flow: list responses expose only the row id plus a
    # token_hint (raw tokens are sanitized out), so clients unregister by id.
    try:
        parsed_token_id = int(token_id)
    except (TypeError, ValueError):
        raise_bad_request("Invalid token id")

    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)
    svc.unregister_token_by_id(current_user.user_id, parsed_token_id)
    return {"success": True}


@router.delete(
    "/push/tokens/{token_value}",
    summary="Unregister push token",
    responses={401: {"model": ErrorResponse}},
)
async def unregister_push_token(
    token_value: str, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)
    svc.unregister_token(current_user.user_id, token_value)
    return {"success": True}
