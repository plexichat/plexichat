"""
Mixin providing WebSocket broadcast helpers for message events.

Encapsulates the fire-and-forget dispatch of MESSAGE_CREATE,
MESSAGE_UPDATE, and MESSAGE_DELETE events.

Events are built exclusively through the shared payload builders in
:mod:`src.core.events.message_events` so ID stringification, the
canonical ``server_id`` scope key (plus the legacy ``guild_id`` alias),
and routing/intent metadata stay uniform across every producer.
"""

import asyncio
from typing import Any, Optional, Tuple

import utils.logger as logger


def _resolve_recipients(
    servers_mod: Any,
    messaging: Any,
    channel_id: int,
    conversation_id: Optional[int],
    actor_id,
    server_id_hint: Optional[int] = None,
) -> Tuple[list, Optional[int]]:
    """Resolve the user ids a message event must reach.

    For server channels only members that can actually VIEW the channel
    (``channels.view`` permission) receive the event — never the entire
    member list, which would leak message content to members barred from
    private/restricted channels. Falls back to conversation participants
    for DM/group conversations.

    ``server_id_hint`` lets system actors that are not server members
    (AutoMod, background scans — user id 0 or a bot id that may not be a
    member) still drive the per-member ``channels.view`` filtering: the
    hint only supplies the channel's server scope, every recipient is
    still individually permission-checked below.

    Returns ``(user_ids, server_id)``; ``server_id`` is resolved once per
    broadcast here so callers never need a second channel lookup.
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
        elif server_id_hint:
            # System-actor path: the actor cannot pass the membership check
            # inside get_channel, so the caller-supplied scope is used purely
            # as the filter domain — recipients are still verified per member.
            server_id = int(server_id_hint)
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

    return user_ids, server_id


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
            from src.core.events import build_message_create

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def broadcast_task():
                    try:
                        user_ids, resolved_server_id = _resolve_recipients(
                            servers_mod,
                            messaging,
                            cid,
                            conversation_id,
                            current_user.user_id,
                        )

                        if user_ids:
                            event = build_message_create(
                                response.model_dump(),
                                channel_id=cid,
                                server_id=resolved_server_id or server_id,
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
            from src.core.events import build_message_update

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def dispatch_task():
                    try:
                        user_ids, event_server_id = _resolve_recipients(
                            servers_mod,
                            messaging,
                            cid,
                            conversation_id,
                            current_user.user_id,
                        )

                        if user_ids:
                            event = build_message_update(
                                response.model_dump(),
                                channel_id=cid,
                                server_id=event_server_id,
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
            from src.core.events import build_message_delete

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def dispatch_task():
                    try:
                        user_ids, server_id = _resolve_recipients(
                            servers_mod,
                            messaging,
                            int(channel_id),
                            int(conversation_id),
                            current_user.user_id,
                        )

                        if user_ids:
                            event = build_message_delete(
                                mid,
                                channel_id,
                                server_id=server_id,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(f"Failed to broadcast MESSAGE_DELETE: {e}")

                asyncio.create_task(dispatch_task())
        except Exception:
            pass
