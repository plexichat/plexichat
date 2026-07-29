from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import raise_bad_request

router = APIRouter()


class PushTokenRequest(BaseModel):
    token: str = Field(..., min_length=1, description="Push notification token")
    platform: str = Field(..., description="Platform: ios, android, or web")
    device_id: Optional[str] = Field(None, description="Device identifier")
    app_version: Optional[str] = Field(None, description="App version string")


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
        )
        return {"success": True, "token_record": result}
    except ValueError as e:
        raise_bad_request(str(e))


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
    return {"tokens": tokens}


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
