"""
Mixin providing WebSocket broadcast helpers for message events.

Encapsulates the fire-and-forget dispatch of MESSAGE_CREATE,
MESSAGE_UPDATE, and MESSAGE_DELETE events.
"""

import asyncio
from typing import Any, Optional

import utils.logger as logger


class BroadcastMixin:
    async def _broadcast_message_create(
        self,
        response,
        server_id: Optional[int],
        cid: int,
        servers_mod: Any,
        messaging: Any,
        current_user,
    ) -> None:
        """Broadcast MESSAGE_CREATE event via WebSocket (fire and forget)."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def broadcast_task():
                    try:
                        user_ids = []
                        if server_id and servers_mod:
                            try:
                                user_ids = servers_mod.get_member_user_ids(server_id)
                            except Exception:
                                pass

                        if not user_ids and messaging:
                            try:
                                participants = messaging.get_participants(
                                    current_user.user_id, cid
                                )
                                user_ids = [p.user_id for p in (participants or [])]
                            except Exception:
                                pass

                        if user_ids:
                            event = Event(
                                event_type=EventType.MESSAGE_CREATE,
                                data=response.model_dump(),
                                server_id=server_id,
                                channel_id=cid,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.warning(f"Failed to broadcast MESSAGE_CREATE: {e}")

                asyncio.create_task(broadcast_task())
        except Exception:
            pass

    async def _broadcast_message_update(
        self,
        response,
        cid: int,
        servers_mod: Any,
        messaging: Any,
        current_user,
    ) -> None:
        """Broadcast MESSAGE_UPDATE event via WebSocket (fire and forget)."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def dispatch_task():
                    try:
                        user_ids = []
                        if servers_mod:
                            try:
                                channel = servers_mod.get_channel(
                                    cid, current_user.user_id
                                )
                                if channel:
                                    server_id = getattr(channel, "server_id", None)
                                    if server_id:
                                        user_ids = servers_mod.get_member_user_ids(
                                            server_id
                                        )
                            except Exception:
                                pass

                        if not user_ids and messaging:
                            try:
                                participants = messaging.get_participants(
                                    current_user.user_id, cid
                                )
                                user_ids = [p.user_id for p in (participants or [])]
                            except Exception:
                                pass

                        if user_ids:
                            event_server_id = None
                            if servers_mod:
                                try:
                                    channel = servers_mod.get_channel(
                                        cid, current_user.user_id
                                    )
                                    if channel:
                                        event_server_id = getattr(
                                            channel, "server_id", None
                                        )
                                except Exception:
                                    pass

                            event = Event(
                                event_type=EventType.MESSAGE_UPDATE,
                                data=response.model_dump(),
                                server_id=event_server_id,
                                channel_id=cid,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(f"Failed to broadcast MESSAGE_UPDATE: {e}")

                asyncio.create_task(dispatch_task())
        except Exception:
            pass

    async def _broadcast_message_delete(
        self,
        mid: int,
        channel_id: str,
        cid: int,
        servers_mod: Any,
        messaging: Any,
        current_user,
    ) -> None:
        """Broadcast MESSAGE_DELETE event via WebSocket (fire and forget)."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def dispatch_task():
                    try:
                        server_id = None
                        user_ids = []
                        actual_channel_id = int(channel_id)

                        if servers_mod:
                            try:
                                channel = servers_mod.get_channel(
                                    actual_channel_id, current_user.user_id
                                )
                                if channel:
                                    server_id = getattr(channel, "server_id", None)
                                    if server_id:
                                        user_ids = servers_mod.get_member_user_ids(
                                            server_id
                                        )
                            except Exception:
                                pass

                        if not user_ids and messaging:
                            try:
                                participants = messaging.get_participants(
                                    current_user.user_id, cid
                                )
                                user_ids = [p.user_id for p in (participants or [])]
                            except Exception:
                                pass

                        if user_ids:
                            event = Event(
                                event_type=EventType.MESSAGE_DELETE,
                                data={
                                    "id": str(mid),
                                    "channel_id": str(actual_channel_id),
                                    "server_id": str(server_id) if server_id else None,
                                },
                                server_id=server_id,
                                channel_id=actual_channel_id,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(f"Failed to broadcast MESSAGE_DELETE: {e}")

                asyncio.create_task(dispatch_task())
        except Exception:
            pass
