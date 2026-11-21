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


def _fanout_safe_message_body(response) -> dict:
    """Strip viewer-specific fields from a message body before fan-out.

    MESSAGE_CREATE / MESSAGE_UPDATE serializers build a single
    ``MessageResponse`` with a concrete viewer — the sender, editor or
    pinner — and the broadcast helper dumps that one body to *every* channel
    recipient. Several fields in that dump are resolved through the
    serializer's viewer window and must never cross that boundary:

    * ``poll_results`` / ``poll.total_votes``: ``get_results(viewer)`` returns
      per-option counts, the voter headcount and the viewer's own votes for
      AFTER_VOTE / AFTER_END polls. For a poll-bearing message the actor is
      usually the author, so the fan-out body hands every recipient the
      author's private results window.
    * ``status`` / ``read`` / ``read_by``: documented as "for the current
      user" — one viewer's delivery/read state is not another's.
    * reaction ``me`` flags: whether *that* viewer reacted, not the audience's.

    The HTTP response returned to the actor keeps its full viewer-specific
    fields; this helper only sanitizes the copy that goes on the wire to the
    many. Fail closed: unknown/oddly-shaped values are dropped rather than
    forwarded.
    """
    data = response.model_dump() if hasattr(response, "model_dump") else dict(response)

    # Poll results are viewer-scoped. The plain poll skeleton (question /
    # options) survives for UI continuity, but its totals are viewer-scoped
    # too — mirror the tombstone serializer's fail-closed treatment.
    data["poll_results"] = None
    poll = data.get("poll")
    if isinstance(poll, dict):
        poll["total_votes"] = None
        options = poll.get("options")
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict):
                    option["vote_count"] = None

    # Read state is "for the current user" (see MessageResponse field docs).
    data["read"] = False
    data["read_by"] = []
    data["status"] = None

    # Reaction "me" flags describe the serializer's viewer, not the receiver.
    reactions = data.get("reactions")
    if isinstance(reactions, list):
        for reaction in reactions:
            if isinstance(reaction, dict):
                reaction["me"] = False

    return data


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
    ONLY for DM/group conversations.

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
    is_server_channel = False

    if servers_mod:
        try:
            channel = servers_mod.get_channel(channel_id, actor_id)
        except Exception:
            channel = None
        if channel is not None:
            is_server_channel = True
            server_id = getattr(channel, "server_id", None)
        elif server_id_hint:
            # System-actor path: the actor cannot pass the membership check
            # inside get_channel, so the caller-supplied scope is used purely
            # as the filter domain — recipients are still verified per member.
            is_server_channel = True
            server_id = int(server_id_hint)
        else:
            # FAIL CLOSED on an unresolved channel. ``get_channel`` returns
            # None for several distinct reasons: the id is a DM/group id, the
            # actor is no longer a member, the actor lost ``channels.view``,
            # the channel was deleted, or the lookup itself failed. Treating
            # every None as "DM" and falling through to the raw conversation
            # participant list reintroduces the audience leak this helper
            # exists to prevent — a server channel whose viewer walk failed
            # would be delivered to every participant via the DM fallback.
            # Probe membership-agnostically: if the id names a server channel
            # at all, keep it in the server-channel path (user_ids stays empty
            # when no server_id could be resolved, so no event is broadcast).
            channel_exists = getattr(servers_mod, "channel_exists", None)
            if callable(channel_exists):
                try:
                    if channel_exists(channel_id):
                        is_server_channel = True
                except Exception:
                    # If we cannot even prove the id is NOT a server channel,
                    # fail closed rather than risk a server-wide leak.
                    is_server_channel = True
        if is_server_channel and server_id:
            # The member listing is the expensive part of fanout: a burst of
            # messages to one channel re-runs the same ``srv_members`` query
            # for every send. Memoize it in a bounded, short-TTL cache that is
            # invalidated on every membership write. The ``channels.view``
            # check below still runs per member on every broadcast, so a
            # permission change takes effect immediately without extra
            # invalidation.
            from src.core.servers.manager.channel_audience_cache import (
                get_cached_channel_members,
            )

            members = get_cached_channel_members(servers_mod, server_id)
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
                except Exception as member_error:
                    logger.debug(f"broadcast member lookup failed: {member_error}")
                    continue
            # An empty permission-filtered audience is authoritative. Falling
            # back to the raw conversation participants here would reconstruct
            # the server-wide audience for a restricted channel the members
            # cannot view — the exact leak this filter exists to prevent.
            # (Mirrors core.events.gateway_emit.resolve_channel_event_recipients.)

    # The participant fallback is for DMs/groups ONLY. A server channel that
    # resolved to zero viewers must broadcast to nobody, not to the whole
    # conversation — a failed/empty permission walk must fail closed.
    if not user_ids and messaging and not is_server_channel:
        participants_conversation_id = (
            conversation_id if conversation_id is not None else channel_id
        )
        try:
            participants = messaging.get_participants(
                actor_id, participants_conversation_id
            )
            user_ids = [p.user_id for p in (participants or [])]
        except Exception as participant_error:
            logger.debug(f"broadcast participant lookup failed: {participant_error}")

        # Reuse the router's two-way block filter: a blocked party must not
        # receive the actor's message events just because this emitter hands
        # the dispatcher an explicit user_ids list instead of routing by
        # server_id. The helper keeps the audience when no relationship source
        # is wired (matching the router), but a failing filter must fail closed.
        if user_ids:
            try:
                from src.core.events.gateway_emit import (
                    _dm_participants_without_blocks,
                )

                user_ids = _dm_participants_without_blocks(user_ids, actor_id)
            except Exception as filter_error:
                logger.debug(f"DM block filter unavailable: {filter_error}")
                # The block filter is authoritative for an explicit audience.
                # Without it no participant is proven safe, so deliver to
                # nobody rather than widening back to the unfiltered list.
                user_ids = []

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
                                _fanout_safe_message_body(response),
                                channel_id=cid,
                                server_id=resolved_server_id or server_id,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.warning(f"Failed to broadcast MESSAGE_CREATE: {e}")

                asyncio.create_task(broadcast_task())
        except Exception as task_error:
            logger.debug(f"broadcast task scheduling failed: {task_error}")

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
                                _fanout_safe_message_body(response),
                                channel_id=cid,
                                server_id=event_server_id,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(f"Failed to broadcast MESSAGE_UPDATE: {e}")

                asyncio.create_task(dispatch_task())
        except Exception as task_error:
            logger.debug(f"dispatch task scheduling failed: {task_error}")

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
        except Exception as task_error:
            logger.debug(f"dispatch task scheduling failed: {task_error}")
