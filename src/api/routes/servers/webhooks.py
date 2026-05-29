from typing import List
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import WebhookResponse
from src.api.schemas.common import SnowflakeID, ErrorResponse

import utils.logger as logger

router = APIRouter()


@router.get(
    "/{server_id}/webhooks",
    response_model=List[WebhookResponse],
    summary="List server webhooks",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_webhooks(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[WebhookResponse]:
    webhooks_mod = api.get_webhooks()
    if not webhooks_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Webhooks module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        webhooks = webhooks_mod.get_server_webhooks(current_user.user_id, sid)
        return [
            WebhookResponse(
                id=SnowflakeID(w.id),
                channel_id=SnowflakeID(w.channel_id),
                server_id=SnowflakeID(w.server_id),
                creator_id=SnowflakeID(w.creator_id)
                if getattr(w, "creator_id", 0)
                else None,
                name=w.name,
                avatar_url=w.avatar_url,
                created_at=w.created_at,
            )
            for w in (webhooks or [])
        ]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to get webhooks for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
