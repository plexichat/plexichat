from typing import List

from fastapi import HTTPException, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import WebhookResponse
from src.api.schemas.common import ErrorResponse, SnowflakeID

import utils.logger as logger

from .base import ChannelBase


class ChannelWebhooksMixin(ChannelBase):
    def _register_routes(self) -> None:
        self.router.add_api_route(
            "/{channel_id}/webhooks",
            self._get_channel_webhooks,
            methods=["GET"],
            response_model=List[WebhookResponse],
            summary="Get channel webhooks",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid channel ID"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Channel not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )

    async def _get_channel_webhooks(
        self, channel_id: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> List[WebhookResponse]:
        webhooks_mod = api.get_webhooks()
        if not webhooks_mod:
            logger.error("Webhooks module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Webhooks module not available"}
                },
            )

        try:
            try:
                cid = int(channel_id)
            except (ValueError, TypeError):
                logger.warning(f"Invalid channel ID format for webhooks: {channel_id}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid channel ID"}},
                )

            try:
                webhooks = await run_in_threadpool(
                    webhooks_mod.get_channel_webhooks, current_user.user_id, cid
                )
                return [
                    WebhookResponse(
                        id=SnowflakeID(w.id),
                        channel_id=SnowflakeID(w.channel_id),
                        server_id=SnowflakeID(w.server_id),
                        creator_id=SnowflakeID(getattr(w, "creator_id", 0))
                        if getattr(w, "creator_id", 0)
                        else None,
                        name=w.name,
                        avatar_url=w.avatar_url,
                        created_at=w.created_at,
                    )
                    for w in (webhooks or [])
                ]
            except Exception as e:
                exc_name = type(e).__name__
                if "NotFound" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                elif "Permission" in exc_name or "Access" in exc_name:
                    logger.warning(
                        f"User {current_user.user_id} denied access to webhooks for channel {cid}"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": str(e)}},
                    )

                logger.error(
                    f"Failed to get webhooks for channel {cid}: {e}", exc_info=True
                )
                raise HTTPException(
                    status_code=500, detail={"error": {"code": 500, "message": str(e)}}
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in get_channel_webhooks for {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
