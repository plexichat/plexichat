"""
Gateway event emission helpers - Shared module for dispatching
real-time gateway events to connected clients.

This module centralizes emission of every client-facing gateway event,
especially the ones that were previously defined-only (never emitted) in
the :class:`src.core.events.types.EventType` enum. Routes and core modules
should call these helpers instead of constructing :class:`Event` objects and
calling the dispatcher directly, so that routing and intent filtering stay
consistent in one place.

All helpers are intentionally non-raising: a failure to dispatch must never
break the underlying REST/CRUD operation that triggered it.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger

from .types import EventType


def _emit(
    event_type: EventType,
    data: Dict[str, Any],
    *,
    user_ids: Optional[List[int]] = None,
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    exclude_user_ids: Optional[List[int]] = None,
) -> None:
    """Dispatch a gateway event, swallowing errors so callers stay safe."""
    try:
        from src.core import events

        if not events.is_setup():
            return
        events.dispatch(
            events.Event(event_type=event_type, data=data),
            user_ids=user_ids,
            server_id=server_id,
            channel_id=channel_id,
            exclude_user_ids=exclude_user_ids,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"gateway_emit: failed to dispatch {event_type.value}: {e}")


# --------------------------------------------------------------------------
# User / profile
# --------------------------------------------------------------------------
def emit_user_update(
    user: Dict[str, Any], exclude_user_id: Optional[int] = None
) -> None:
    """Notify connected clients that a user's profile changed (avatar, username...)."""
    uid = user.get("id")
    _emit(
        EventType.USER_UPDATE,
        {"user": user},
        user_ids=[int(uid)] if uid is not None else None,
        exclude_user_ids=[exclude_user_id] if exclude_user_id is not None else None,
    )


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
def emit_message_delete_bulk(
    channel_id: int,
    message_ids: List[int],
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that a batch of messages was deleted from a channel."""
    _emit(
        EventType.MESSAGE_DELETE_BULK,
        {
            "channel_id": channel_id,
            "guild_id": guild_id,
            "message_ids": message_ids,
        },
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


def emit_message_reaction_remove_all(
    channel_id: int,
    message_id: int,
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that all reactions on a message were cleared."""
    _emit(
        EventType.MESSAGE_REACTION_REMOVE_ALL,
        {
            "channel_id": channel_id,
            "message_id": message_id,
            "guild_id": guild_id,
        },
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


def emit_channel_pins_update(
    channel_id: int,
    guild_id: Optional[int] = None,
    pinned_message_ids: Optional[List[int]] = None,
) -> None:
    """Notify clients that the pinned-message set of a channel changed."""
    _emit(
        EventType.CHANNEL_PINS_UPDATE,
        {
            "channel_id": channel_id,
            "guild_id": guild_id,
            "pinned_message_ids": pinned_message_ids or [],
        },
        channel_id=channel_id,
        server_id=guild_id,
    )


# --------------------------------------------------------------------------
# Server roles
# --------------------------------------------------------------------------
def emit_guild_role_create(server_id: int, role: Dict[str, Any]) -> None:
    _emit(
        EventType.GUILD_ROLE_CREATE,
        {"guild_id": server_id, "role": role},
        server_id=server_id,
    )


def emit_guild_role_update(server_id: int, role: Dict[str, Any]) -> None:
    _emit(
        EventType.GUILD_ROLE_UPDATE,
        {"guild_id": server_id, "role": role},
        server_id=server_id,
    )


def emit_guild_role_delete(server_id: int, role_id: int) -> None:
    _emit(
        EventType.GUILD_ROLE_DELETE,
        {"guild_id": server_id, "role_id": role_id},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Server bans
# --------------------------------------------------------------------------
def emit_guild_ban_add(
    server_id: int, user_id: int, reason: Optional[str] = None
) -> None:
    _emit(
        EventType.GUILD_BAN_ADD,
        {"guild_id": server_id, "user_id": user_id, "reason": reason},
        server_id=server_id,
    )


def emit_guild_ban_remove(server_id: int, user_id: int) -> None:
    _emit(
        EventType.GUILD_BAN_REMOVE,
        {"guild_id": server_id, "user_id": user_id},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Invites
# --------------------------------------------------------------------------
def emit_invite_create(server_id: int, invite: Dict[str, Any]) -> None:
    _emit(
        EventType.INVITE_CREATE,
        {"guild_id": server_id, "invite": invite},
        server_id=server_id,
    )


def emit_invite_delete(server_id: int, code: str) -> None:
    _emit(
        EventType.INVITE_DELETE,
        {"guild_id": server_id, "code": code},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------
def emit_webhooks_update(server_id: int, channel_id: Optional[int] = None) -> None:
    _emit(
        EventType.WEBHOOKS_UPDATE,
        {"guild_id": server_id, "channel_id": channel_id},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------
def emit_thread_create(thread: Dict[str, Any]) -> None:
    _emit(
        EventType.THREAD_CREATE,
        thread,
        channel_id=thread.get("parent_id"),
        server_id=thread.get("guild_id"),
    )


def emit_thread_update(thread: Dict[str, Any]) -> None:
    _emit(
        EventType.THREAD_UPDATE,
        thread,
        channel_id=thread.get("parent_id"),
        server_id=thread.get("guild_id"),
    )


def emit_thread_delete(
    thread_id: int,
    guild_id: Optional[int] = None,
    parent_id: Optional[int] = None,
) -> None:
    _emit(
        EventType.THREAD_DELETE,
        {"id": thread_id, "guild_id": guild_id, "parent_id": parent_id},
        channel_id=parent_id,
        server_id=guild_id,
    )


# --------------------------------------------------------------------------
# Security / account alerts (bridges the audit log to live clients)
# --------------------------------------------------------------------------
def emit_security_alert(
    user_id: int,
    action: str,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Push a real-time security/account alert to a specific user's sessions.

    This is the live counterpart of the audit log. Examples: a session was
    revoked, 2FA was disabled, the password changed, or the account was
    locked. The client renders these via the SECURITY_ALERT dispatch event.
    """
    _emit(
        EventType.SECURITY_ALERT,
        {
            "user_id": user_id,
            "action": action,
            "detail": detail or {},
        },
        user_ids=[user_id],
    )
