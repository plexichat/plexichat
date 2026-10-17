from typing import List

from fastapi import HTTPException, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import WebhookResponse
from src.api.schemas.common import ErrorResponse, SnowflakeID

import utils.logger as logger

from .base import ChannelBase
from ..exception_mapping import raise_mapped


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

            # F8 FIX: existence/access pre-check consistent with channel CRUD
            # so correctness does not depend on the webhooks module raising
            # correctly-named exceptions (dual probe: 404 nonexistent vs 403
            # exists-but-blocked).
            servers_mod = api.get_servers()
            messaging_mod = api.get_messaging()

            has_access = False
            if servers_mod:
                try:
                    if servers_mod.get_channel(cid, current_user.user_id):
                        has_access = True
                except Exception:
                    pass
            if not has_access and messaging_mod:
                try:
                    if messaging_mod.get_conversation(cid, current_user.user_id):
                        has_access = True
                except Exception:
                    pass

            if not has_access:
                exists = False
                if servers_mod:
                    try:
                        exists = bool(servers_mod.channel_exists(cid))
                    except Exception:
                        exists = False
                if not exists and messaging_mod:
                    try:
                        exists = bool(messaging_mod.conversation_exists(cid))
                    except Exception:
                        exists = False
                if not exists:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Channel not found",
                            }
                        },
                    )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Channel access denied"}},
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
                raise_mapped(
                    e,
                    not_found_message="Channel not found",
                    permission_message="Access denied",
                    invalid_message="Invalid request",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in get_channel_webhooks for {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )
