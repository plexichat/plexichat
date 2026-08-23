"""
Mixin providing WebSocket broadcast helpers for message events.

Encapsulates the fire-and-forget dispatch of MESSAGE_CREATE,
MESSAGE_UPDATE, and MESSAGE_DELETE events.
"""

import asyncio
from typing import Any, Optional

import utils.logger as logger


def _resolve_recipients(
    servers_mod: Any,
    messaging: Any,
    channel_id: int,
    conversation_id: Optional[int],
    actor_id,
) -> list:
    """Resolve the user ids a message event must reach.

    For server channels only members that can actually VIEW the channel
    (``channels.view`` permission) receive the event — never the entire
    member list, which would leak message content to members barred from
    private/restricted channels. Falls back to conversation participants
    for DM/group conversations.
    """
    user_ids: list = []
    server_id = None

    if servers_mod:
        try:
            channel = servers_mod.get_channel(channel_id, actor_id)
        except Exception:
            channel = None
        if channel:
            server_id = getattr(channel, "server_id", None)
            if server_id:
                try:
                    members = servers_mod.get_member_user_ids(server_id)
                except Exception:
                    members = []
                has_permission = getattr(servers_mod, "has_permission", None)
                for member_id in members:
                    if has_permission is None:
                        # Without a permission API we cannot verify channel
                        # access; exclude rather than over-broadcast.
                        continue
                    try:
                        if has_permission(
                            member_id, server_id, "channels.view", channel_id
                        ):
                            user_ids.append(member_id)
                    except Exception:
                        continue

    if not user_ids and messaging:
        participants_conversation_id = (
            conversation_id if conversation_id is not None else channel_id
        )
        try:
            participants = messaging.get_participants(
                actor_id, participants_conversation_id
            )
            user_ids = [p.user_id for p in (participants or [])]
        except Exception:
            pass

    return user_ids


class BroadcastMixin:
    async def _broadcast_message_create(
        self,
        response,
        server_id: Optional[int],
        cid: int,
        servers_mod: Any,
        messaging: Any,
        current_user,
        conversation_id: Optional[int] = None,
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
                        user_ids = _resolve_recipients(
                            servers_mod,
                            messaging,
                            cid,
                            conversation_id,
                            current_user.user_id,
                        )

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
        conversation_id: Optional[int] = None,
    ) -> None:
        """Broadcast MESSAGE_UPDATE event via WebSocket (fire and forget).

        ``conversation_id`` is the edited message's ACTUAL conversation id;
        recipients are always derived from it so an edit broadcast can never
        leak content into a different conversation.
        """
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def dispatch_task():
                    try:
                        user_ids = _resolve_recipients(
                            servers_mod,
                            messaging,
                            cid,
                            conversation_id,
                            current_user.user_id,
                        )

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
        channel_id: int,
        conversation_id: int,
        servers_mod: Any,
        messaging: Any,
        current_user,
    ) -> None:
        """Broadcast MESSAGE_DELETE event via WebSocket (fire and forget).

        ``channel_id`` labels the event with the URL channel the client
        addressed; ``conversation_id`` MUST be the deleted message's actual
        conversation and drives participant resolution.
        """
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def dispatch_task():
                    try:
                        server_id = None
                        if servers_mod:
                            try:
                                channel = servers_mod.get_channel(
                                    int(channel_id), current_user.user_id
                                )
                                if channel:
                                    server_id = getattr(channel, "server_id", None)
                            except Exception:
                                pass

                        user_ids = _resolve_recipients(
                            servers_mod,
                            messaging,
                            int(channel_id),
                            int(conversation_id),
                            current_user.user_id,
                        )

                        if user_ids:
                            event = Event(
                                event_type=EventType.MESSAGE_DELETE,
                                data={
                                    "id": str(mid),
                                    "channel_id": str(channel_id),
                                    "server_id": str(server_id) if server_id else None,
                                },
                                server_id=server_id,
                                channel_id=int(channel_id),
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(f"Failed to broadcast MESSAGE_DELETE: {e}")

                asyncio.create_task(dispatch_task())
        except Exception:
            pass
