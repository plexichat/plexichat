import asyncio
from typing import Dict

from fastapi import HTTPException, Depends, Request
from starlette.concurrency import run_in_threadpool

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.request_guards import read_body_capped
from src.api.routes.users.helpers import authenticated_cache_key, require_user_token
from src.api.schemas.servers import ChannelResponse, ChannelUpdateRequest
from src.api.schemas.common import ErrorResponse
from src.core.database import cached

import utils.logger as logger

from .base import ChannelBase
from .helpers import (
    bounded_bulk_batch,
    require_channel_snowflake,
    validate_channel_fields,
)
from ..exception_mapping import raise_mapped


# A PATCH only carries a handful of short fields; anything near this size is
# an attempt to make the parser allocate on the server's behalf.
MAX_CHANNEL_UPDATE_BODY = 64 * 1024


async def _cap_channel_update_body(request: Request) -> None:
    """Reject oversized channel-update bodies before Pydantic parses them."""
    await read_body_capped(request, MAX_CHANNEL_UPDATE_BODY)


async def _require_channel_access(channel_id: str, user_id: int) -> None:
    """Live channel/DM access gate that runs before the cached read.

    ``_load_channel`` enforces channel membership / ``channels.view`` (and DM
    participation) in its cold path, but a warm ``@cached`` hit returns before
    that body runs. A viewer whose access was revoked -- kick/ban/leave, a role
    change dropping ``channels.view``, or removal from a group DM -- would
    otherwise keep reading the still-warm channel detail for the whole TTL.
    Re-probe access authoritatively on every request and fail closed: a missing
    module or a failing checker must never let a warm body substitute for a
    denial. The 404-vs-403 mapping mirrors the cached body's contract.
    """
    cid = require_channel_snowflake(channel_id)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    try:
        if servers_mod.channel_exists(cid):
            channel = await run_in_threadpool(servers_mod.get_channel, cid, user_id)
            if not channel:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Channel access denied"}},
                )
            return

        messaging_mod = api.get_messaging()
        if messaging_mod and messaging_mod.conversation_exists(cid):
            conversation = await run_in_threadpool(
                messaging_mod.get_conversation, cid, user_id
            )
            if not conversation:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Channel access denied"}},
                )
            return

        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Channel access gate failed for {channel_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Failed to verify channel access"}
            },
        ) from e


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
            dependencies=[Depends(_cap_channel_update_body)],
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

    # F6 FIX: the 30s per-user cache served stale (pre-revocation) channel
    # representations for up to half a minute after a kick/ban/downgrade.
    # A short TTL keeps the read hot-path benefit while bounding the
    # revocation-staleness window to seconds.
    @cached(
        ttl=5, prefix="channel_api", key_builder=authenticated_cache_key("channel_api")
    )
    async def _load_channel(
        self, channel_id: str, current_user: TokenInfo
    ) -> ChannelResponse:
        """Data-only cold path for a single channel.

        Access is re-probed live by ``_require_channel_access`` in the route
        handler on EVERY request, including cache hits. This body still checks
        access on the cold path so only authorised data is cached, but the
        authoritative revocation gate lives outside the ``@cached`` wrapper.
        """
        require_user_token(current_user)
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
                cid = require_channel_snowflake(channel_id)
            except HTTPException:
                logger.warning(f"Invalid channel ID format: {channel_id}")
                raise

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
                        # 404-vs-403 parity for conversations: a
                        # non-participant who knows a real conversation id
                        # must receive 403, not 404. Use the
                        # membership-agnostic ``conversation_exists`` probe
                        # (the parallel of ``servers_mod.channel_exists``).
                        messaging_mod = api_module.get_messaging()
                        if messaging_mod:
                            ch = messaging_mod.get_conversation(
                                cid, current_user.user_id
                            )
                            if ch:
                                return ch
                            if messaging_mod.conversation_exists(cid):
                                raise HTTPException(
                                    status_code=403,
                                    detail={
                                        "error": {
                                            "code": 403,
                                            "message": "Channel access denied",
                                        }
                                    },
                                )
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
                f"Unexpected error in get_channel for {channel_id}: {e}", exc_info=True
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

    async def _get_channel(
        self, channel_id: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> ChannelResponse:
        """Route handler: live access gate BEFORE the cached channel read."""
        await _require_channel_access(channel_id, current_user.user_id)
        return await self._load_channel(channel_id, current_user)

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
                cid = require_channel_snowflake(channel_id)
            except HTTPException:
                logger.warning(f"Invalid channel ID format: {channel_id}")
                raise

            try:
                update_data = body.model_dump(exclude_unset=True)
                validate_channel_fields(update_data)
                validate_channel_fields(update_data)
                validate_channel_fields(update_data)

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

                            # SECURITY: CHANNEL_UPDATE carries the full channel
                            # representation (name, topic, slowmode, ...). A raw
                            # get_member_user_ids(sid) fan-out delivered that
                            # payload to *every* member of the server, including
                            # members who cannot view a restricted/private
                            # channel. Scope to viewers with channels.view via
                            # the shared resolver (which fails closed) so a
                            # channel's existence and settings are not leaked.
                            from src.core.events.gateway_emit import (
                                resolve_channel_event_recipients,
                            )

                            user_ids = bounded_bulk_batch(
                                resolve_channel_event_recipients(
                                    cid,
                                    guild_id=sid,
                                    actor_id=current_user.user_id,
                                ),
                                "user_ids",
                            )

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
                raise_mapped(
                    e,
                    not_found_message="Channel not found",
                    permission_message="Channel access denied",
                    invalid_message="Invalid channel data",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in update_channel for {channel_id}: {e}",
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
                cid = require_channel_snowflake(channel_id)
            except HTTPException:
                logger.warning(f"Invalid channel ID format: {channel_id}")
                raise

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

                # Resolve the delete event's audience while the channel still
                # exists. After the mutation a channels.view lookup can no
                # longer resolve the row and the shared resolver fails closed
                # to nobody; resolving first keeps the legitimate clients in
                # the audience without leaking the deletion to non-viewers.
                try:
                    from src.core.events.gateway_emit import (
                        resolve_channel_event_recipients,
                    )

                    delete_user_ids = bounded_bulk_batch(
                        resolve_channel_event_recipients(
                            cid,
                            guild_id=sid,
                            actor_id=current_user.user_id,
                        ),
                        "user_ids",
                    )
                except Exception as audience_error:
                    logger.debug(
                        f"Failed to scope CHANNEL_DELETE recipients for {cid}: "
                        f"{audience_error}"
                    )
                    delete_user_ids = []

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
                            # Pre-resolved before the delete so the audience
                            # reflects the channel's pre-deletion viewer set.
                            user_ids = delete_user_ids

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
                raise_mapped(
                    e,
                    not_found_message="Channel not found",
                    permission_message="Channel access denied",
                    invalid_message="Invalid request",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in delete_channel for {channel_id}: {e}",
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
