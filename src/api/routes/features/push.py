from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import raise_bad_request
from src.core.push.exceptions import PermissionDeniedError

router = APIRouter()


class PushTokenRequest(BaseModel):
    token: str = Field(..., min_length=1, description="Push notification token")
    platform: str = Field(..., description="Platform: ios, android, or web")
    device_id: Optional[str] = Field(None, description="Device identifier")
    app_version: Optional[str] = Field(None, description="App version string")
    p256dh: Optional[str] = Field(None, description="Web Push P-256 public key")
    auth: Optional[str] = Field(None, description="Web Push auth secret")


@router.post(
    "/push/tokens",
    summary="Register push notification token",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def register_push_token(
    body: PushTokenRequest, current_user: TokenInfo = Depends(get_current_user)
):
    if body.platform not in ("ios", "android", "web"):
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
        return {
            "success": True,
            "token_record": PushManager._sanitize_token_record(result),
        }
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
    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)
    tokens = svc.get_user_tokens(current_user.user_id)
    # SECURITY: get_user_tokens returns decrypted credentials for delivery
    # use only; strip them before serialising to the client.
    return {"tokens": [PushManager._sanitize_token_record(t) for t in tokens]}


@router.delete(
    "/push/tokens/id/{token_id}",
    summary="Unregister push token by id",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def unregister_push_token_by_id(
    token_id: str, current_user: TokenInfo = Depends(get_current_user)
):
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
    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)
    svc.unregister_token(current_user.user_id, token_value)
    return {"success": True}
