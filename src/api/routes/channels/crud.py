import asyncio
from typing import Dict

from fastapi import HTTPException, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import ChannelResponse, ChannelUpdateRequest
from src.api.schemas.common import ErrorResponse
from src.core.database import cached

import utils.logger as logger

from .base import ChannelBase


class ChannelCRUDMixin(ChannelBase):
    def _register_routes(self) -> None:
        self.router.add_api_route(
            "/{channel_id}",
            self._get_channel,
            methods=["GET"],
            response_model=ChannelResponse,
            summary="Get channel",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid channel ID"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Channel not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/{channel_id}",
            self._update_channel,
            methods=["PATCH"],
            response_model=ChannelResponse,
            summary="Update channel",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid channel ID or data",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Channel not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/{channel_id}",
            self._delete_channel,
            methods=["DELETE"],
            summary="Delete channel",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid channel ID"},
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Channel not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )

    @cached(ttl=30, prefix="channel_api")
    async def _get_channel(
        self, channel_id: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> ChannelResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                cid = int(channel_id)
            except (ValueError, TypeError):
                logger.warning(f"Invalid channel ID format: {channel_id}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid channel ID"}},
                )

            try:

                def _get_channel_sync():
                    import src.api as api_module

                    db = api_module.get_db()
                    try:
                        # SELFTEST FIX: split 404 (channel gone) from
                        # 403 (exists, caller blocked) by pre-checking
                        # the membership-agnostic existence probe.
                        # If the channel exists as a server channel
                        # but the membership-aware ``get_channel``
                        # returns None, the caller lacks permission
                        # -> 403. If the channel doesn't exist at all,
                        # fall through to the messaging-conversation
                        # path so DMs are still findable by id.
                        if servers_mod.channel_exists(cid):
                            ch = servers_mod.get_channel(cid, current_user.user_id)
                            if not ch:
                                raise HTTPException(
                                    status_code=403,
                                    detail={
                                        "error": {
                                            "code": 403,
                                            "message": "Channel access denied",
                                        }
                                    },
                                )
                            return ch
                        # TODO(404-vs-403 parity): messaging_mod exposes
                        # only the membership-aware ``get_conversation``,
                        # so a non-participant who happens to know a
                        # real conversation id here sees 404 instead of
                        # 403. This mirrors the original code's
                        # behaviour and is left for a follow-up that
                        # adds a membership-agnostic
                        # ``messaging_mod.conversation_exists`` probe
                        # (the parallel of ``servers_mod.channel_exists``
                        # used above). Tracked but not fixed in this
                        # pass to keep the bounded change scoped to
                        # server-channel routes per the selftest intent.
                        messaging_mod = api_module.get_messaging()
                        if messaging_mod:
                            ch = messaging_mod.get_conversation(
                                cid, current_user.user_id
                            )
                            if ch:
                                return ch
                        return None
                    finally:
                        if db:
                            db.close()

                channel = await run_in_threadpool(_get_channel_sync)

                if not channel:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                return self._channel_to_response(channel, current_user.user_id)
            except HTTPException:
                raise
            except Exception as e:
                exc_name = type(e).__name__
                if "NotFound" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                elif "Access" in exc_name or "Permission" in exc_name:
                    logger.warning(
                        f"User {current_user.user_id} denied access to channel {cid}"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": "Access denied"}},
                    )

                logger.error(
                    f"Failed to get channel {cid} for user {current_user.user_id}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500, detail={"error": {"code": 500, "message": str(e)}}
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in get_channel for {channel_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def _update_channel(
        self,
        channel_id: str,
        body: ChannelUpdateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> ChannelResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                cid = int(channel_id)
            except (ValueError, TypeError):
                logger.warning(f"Invalid channel ID format for update: {channel_id}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid channel ID"}},
                )

            try:
                update_data = body.model_dump(exclude_unset=True)

                def _update_channel_sync():
                    import src.api as api_module

                    db = api_module.get_db()
                    try:
                        # SELFTEST FIX: pre-check channel existence +
                        # access so the endpoint distinguishes 404
                        # (channel gone) from 403 (exists but caller
                        # lacks permission). Previously the manager
                        # raised PermissionDenied when the user wasn't
                        # a member, which surfaced as 500 or was
                        # being conflated with 404 by the auto-loop
                        # when the channel itself had been deleted.
                        if not servers_mod.channel_exists(cid):
                            raise HTTPException(
                                status_code=404,
                                detail={
                                    "error": {
                                        "code": 404,
                                        "message": "Channel not found",
                                    }
                                },
                            )
                        existing = servers_mod.get_channel(cid, current_user.user_id)
                        if not existing:
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "error": {
                                        "code": 403,
                                        "message": "Channel access denied",
                                    }
                                },
                            )
                        return servers_mod.update_channel(
                            current_user.user_id, cid, **update_data
                        )
                    finally:
                        if db:
                            db.close()

                channel = await run_in_threadpool(_update_channel_sync)

                response = self._channel_to_response(channel, current_user.user_id)
                sid = channel.server_id

                async def dispatch_channel_update():
                    try:
                        from src.api.websocket import (
                            get_dispatcher,
                            is_setup as ws_is_setup,
                        )
                        from src.core.events.models import Event
                        from src.core.events.types import EventType

                        if ws_is_setup():
                            dispatcher = get_dispatcher()

                            def _get_member_ids_sync():
                                import src.api as api_module

                                db = api_module.get_db()
                                try:
                                    return servers_mod.get_member_user_ids(sid)
                                finally:
                                    if db:
                                        db.close()

                            user_ids = await run_in_threadpool(_get_member_ids_sync)

                            if user_ids:
                                event = Event(
                                    event_type=EventType.CHANNEL_UPDATE,
                                    data=response.model_dump(),
                                    server_id=sid,
                                    channel_id=cid,
                                )
                                await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(
                            f"Failed to broadcast CHANNEL_UPDATE for channel {cid}: {e}"
                        )

                asyncio.create_task(dispatch_channel_update())

                return response
            except HTTPException:
                raise
            except Exception as e:
                exc_name = type(e).__name__
                if "NotFound" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                elif "Permission" in exc_name:
                    logger.warning(
                        f"User {current_user.user_id} denied permission to update channel {cid}"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": str(e)}},
                    )

                logger.error(
                    f"Failed to update channel {cid} for user {current_user.user_id}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500, detail={"error": {"code": 500, "message": str(e)}}
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in update_channel for {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def _delete_channel(
        self, channel_id: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> Dict[str, bool]:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                cid = int(channel_id)
            except (ValueError, TypeError):
                logger.warning(f"Invalid channel ID format for deletion: {channel_id}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid channel ID"}},
                )

            try:
                # SELFTEST FIX: pre-check channel existence before
                # fetching with membership-aware get_channel so the
                # endpoint distinguishes 404 (channel gone) from 403
                # (exists, caller blocked). Same pattern as PATCH.
                if not servers_mod.channel_exists(cid):
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if not channel:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Channel access denied",
                            }
                        },
                    )

                sid = channel.server_id

                servers_mod.delete_channel(current_user.user_id, cid)

                async def dispatch_channel_delete():
                    try:
                        from src.api.websocket import (
                            get_dispatcher,
                            is_setup as ws_is_setup,
                        )
                        from src.core.events.models import Event
                        from src.core.events.types import EventType

                        if ws_is_setup():
                            dispatcher = get_dispatcher()
                            user_ids = servers_mod.get_member_user_ids(sid)

                            if user_ids:
                                event = Event(
                                    event_type=EventType.CHANNEL_DELETE,
                                    data={"id": str(cid), "server_id": str(sid)},
                                    server_id=sid,
                                    channel_id=cid,
                                )
                                await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(
                            f"Failed to broadcast CHANNEL_DELETE for channel {cid}: {e}"
                        )

                asyncio.create_task(dispatch_channel_delete())

                return {"success": True}
            except HTTPException:
                raise
            except Exception as e:
                exc_name = type(e).__name__
                if "NotFound" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                elif "Permission" in exc_name:
                    logger.warning(
                        f"User {current_user.user_id} denied permission to delete channel {cid}"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": str(e)}},
                    )

                logger.error(
                    f"Failed to delete channel {cid} for user {current_user.user_id}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500, detail={"error": {"code": 500, "message": str(e)}}
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in delete_channel for {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
