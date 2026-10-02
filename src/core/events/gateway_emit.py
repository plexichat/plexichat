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

from typing import Any, Callable, Dict, List, Optional, TypeVar

import utils.logger as logger

from .snowflakes import stringify_snowflake_ids
from .types import EventType

F = TypeVar("F", bound=Callable[..., None])


def _non_raising(fn: F) -> F:
    """Guard a gateway emitter so payload building cannot raise.

    ``_emit`` already swallows dispatch errors, but ``int(...)``/``str(...)``
    coercions run while building the payload dict -- outside that guard. A
    malformed id (e.g. ``user["id"] = "abc"`` or a bad ``revision``) would
    then raise into the REST handler, violating this module's non-raising
    contract. The decorator keeps the guarantee for current and future
    helpers: log at WARNING (so breakage is visible) and return None.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"gateway_emit: failed to build {fn.__name__}: {e}")
            return None

    return wrapper  # type: ignore[return-value]


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
        # C1: a serialization or routing bug in ANY emitter was previously
        # invisible at default log levels (DEBUG). Surface it at WARNING so
        # broken emissions are detectable while staying non-raising.
        logger.warning(f"gateway_emit: failed to dispatch {event_type.value}: {e}")


# Gateway payload id fields are stringified centrally via
# ``events.snowflakes`` so this rule cannot drift between emitters.


# --------------------------------------------------------------------------
# User / profile
# --------------------------------------------------------------------------
@_non_raising
def emit_user_update(
    user: Dict[str, Any], exclude_user_id: Optional[int] = None
) -> None:
    """Notify connected clients that a user's profile changed (avatar, username...)."""
    uid = user.get("id")
    _emit(
        EventType.USER_UPDATE,
        {"user": stringify_snowflake_ids(user)},
        user_ids=[int(uid)] if uid is not None else None,
        exclude_user_ids=[exclude_user_id] if exclude_user_id is not None else None,
    )


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
@_non_raising
def emit_message_delete_bulk(
    channel_id: int,
    message_ids: List[int],
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that a batch of messages was deleted from a channel.

    The scope key is canonical ``server_id`` (matching single
    MESSAGE_DELETE); ``guild_id`` stays as a legacy alias.
    """
    sid = str(guild_id) if guild_id is not None else None
    _emit(
        EventType.MESSAGE_DELETE_BULK,
        {
            "channel_id": str(channel_id),
            "message_ids": [str(mid) for mid in message_ids],
            "server_id": sid,
            "guild_id": sid,
        },
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


@_non_raising
def emit_message_reaction_remove_all(
    channel_id: int,
    message_id: int,
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that all reactions on a message were cleared.

    Payload id fields are stringified (and the acting user included) so the
    shape matches MESSAGE_REACTION_ADD/REMOVE events.
    """
    _emit(
        EventType.MESSAGE_REACTION_REMOVE_ALL,
        {
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "guild_id": str(guild_id) if guild_id is not None else None,
            "user_id": str(actor_id) if actor_id is not None else None,
        },
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


# --------------------------------------------------------------------------
# Polls
# --------------------------------------------------------------------------
@_non_raising
def emit_poll_update(
    poll_id: int,
    message_id: int,
    action: str,
    actor_id: Optional[int] = None,
    is_ended: Optional[bool] = None,
    channel_id: Optional[int] = None,
) -> None:
    """Notify channel viewers that a poll changed (vote/change/remove/close).

    The payload carries identifiers only — no vote counts — because
    result visibility is per-viewer; clients refetch
    ``GET /polls/{id}/results`` with their own credentials when the
    event arrives.
    """
    _emit(
        EventType.POLL_UPDATE,
        {
            "poll_id": str(poll_id),
            "message_id": str(message_id),
            "channel_id": str(channel_id) if channel_id is not None else None,
            "action": action,
            "actor_id": str(actor_id) if actor_id is not None else None,
            "is_ended": is_ended,
        },
        channel_id=channel_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


@_non_raising
def emit_message_reaction_remove_emoji(
    channel_id: int,
    message_id: int,
    emoji: str,
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that every reaction of one emoji was removed by a moderator."""
    _emit(
        EventType.MESSAGE_REACTION_REMOVE_EMOJI,
        {
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "emoji": emoji,
            "guild_id": str(guild_id) if guild_id is not None else None,
            "user_id": str(actor_id) if actor_id is not None else None,
        },
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


@_non_raising
def emit_channel_pins_update(
    channel_id: int,
    guild_id: Optional[int] = None,
    pinned_message_ids: Optional[List[int]] = None,
) -> None:
    """Notify clients that the pinned-message set of a channel changed.

    Payload id fields are stringified so the shape matches the other
    message-family events; ``server_id`` is canonical with ``guild_id``
    kept as a legacy alias (matching MESSAGE_DELETE_BULK).
    """
    sid = str(guild_id) if guild_id is not None else None
    _emit(
        EventType.CHANNEL_PINS_UPDATE,
        {
            "channel_id": str(channel_id),
            "server_id": sid,
            "guild_id": sid,
            "pinned_message_ids": [str(mid) for mid in (pinned_message_ids or [])],
        },
        channel_id=channel_id,
        server_id=guild_id,
    )


# --------------------------------------------------------------------------
# Servers / guilds
# --------------------------------------------------------------------------
@_non_raising
def emit_guild_update(
    server_id: int,
    *,
    name: Optional[str] = None,
    owner_id: Optional[int] = None,
    icon: Optional[str] = None,
) -> None:
    """Notify connected clients that a server's details changed (icon, name...).

    ``create_guild_update`` existed but was never dispatched anywhere, so
    server icon/name changes were silently invisible to connected clients
    until they re-fetched (stale icons in every open session).
    """
    data: Dict[str, Any] = {"id": str(server_id)}
    # ``is not None`` (not truthiness) so callers can explicitly clear a
    # field (e.g. ``icon=""`` when a server icon is deleted).
    if name is not None:
        data["name"] = name
    if owner_id is not None:
        data["owner_id"] = str(owner_id)
    if icon is not None:
        data["icon"] = icon
    _emit(EventType.GUILD_UPDATE, data, server_id=server_id)


# --------------------------------------------------------------------------
# Server roles
# --------------------------------------------------------------------------
@_non_raising
def emit_guild_role_create(server_id: int, role: Dict[str, Any]) -> None:
    _emit(
        EventType.GUILD_ROLE_CREATE,
        {"guild_id": str(server_id), "role": stringify_snowflake_ids(role)},
        server_id=server_id,
    )


@_non_raising
def emit_guild_role_update(server_id: int, role: Dict[str, Any]) -> None:
    _emit(
        EventType.GUILD_ROLE_UPDATE,
        {"guild_id": str(server_id), "role": stringify_snowflake_ids(role)},
        server_id=server_id,
    )


@_non_raising
def emit_guild_role_delete(server_id: int, role_id: int) -> None:
    _emit(
        EventType.GUILD_ROLE_DELETE,
        {"guild_id": str(server_id), "role_id": str(role_id)},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Server bans
# --------------------------------------------------------------------------
@_non_raising
def emit_guild_ban_add(
    server_id: int, user_id: int, reason: Optional[str] = None
) -> None:
    _emit(
        EventType.GUILD_BAN_ADD,
        {"guild_id": str(server_id), "user_id": str(user_id), "reason": reason},
        server_id=server_id,
    )


@_non_raising
def emit_guild_ban_remove(server_id: int, user_id: int) -> None:
    _emit(
        EventType.GUILD_BAN_REMOVE,
        {"guild_id": str(server_id), "user_id": str(user_id)},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Invites
# --------------------------------------------------------------------------
@_non_raising
def emit_invite_create(server_id: int, invite: Dict[str, Any]) -> None:
    _emit(
        EventType.INVITE_CREATE,
        {"guild_id": str(server_id), "invite": stringify_snowflake_ids(invite)},
        server_id=server_id,
    )


@_non_raising
def emit_invite_delete(server_id: int, code: str) -> None:
    _emit(
        EventType.INVITE_DELETE,
        {"guild_id": str(server_id), "code": code},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------
@_non_raising
def emit_webhooks_update(server_id: int, channel_id: Optional[int] = None) -> None:
    _emit(
        EventType.WEBHOOKS_UPDATE,
        {
            "guild_id": str(server_id),
            "channel_id": str(channel_id) if channel_id is not None else None,
        },
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------
@_non_raising
def emit_thread_create(thread: Dict[str, Any]) -> None:
    _emit(
        EventType.THREAD_CREATE,
        stringify_snowflake_ids(thread),
        channel_id=thread.get("parent_id"),
        server_id=thread.get("guild_id"),
    )


@_non_raising
def emit_thread_update(thread: Dict[str, Any]) -> None:
    _emit(
        EventType.THREAD_UPDATE,
        stringify_snowflake_ids(thread),
        channel_id=thread.get("parent_id"),
        server_id=thread.get("guild_id"),
    )


@_non_raising
def emit_thread_delete(
    thread_id: int,
    guild_id: Optional[int] = None,
    parent_id: Optional[int] = None,
) -> None:
    _emit(
        EventType.THREAD_DELETE,
        {
            "id": str(thread_id),
            "guild_id": str(guild_id) if guild_id is not None else None,
            "parent_id": str(parent_id) if parent_id is not None else None,
        },
        channel_id=parent_id,
        server_id=guild_id,
    )


# --------------------------------------------------------------------------
# PlexiSum workbooks (realtime collaboration)
# --------------------------------------------------------------------------
@_non_raising
def emit_workbook_update(
    workbook_id: int,
    revision: int,
    actor_id: int,
    data: Dict[str, Any],
    user_ids: Optional[List[int]] = None,
    exclude_user_ids: Optional[List[int]] = None,
) -> None:
    """Notify everyone with workbook access that a mutation landed.

    The actor is typically excluded (they applied the change locally); every
    other viewer refreshes their viewport when the event arrives. ``data``
    carries extras (e.g. mention targets for comments); the workbook id,
    new revision and actor id are always (re)stated from the explicit
    arguments so a caller cannot emit an identifier-less event. Id fields
    are stringified like the message family; ``revision`` stays numeric.
    """
    payload = dict(data or {})
    payload["workbook_id"] = str(workbook_id)
    payload["revision"] = int(revision)
    payload["actor_id"] = str(actor_id)
    _emit(
        EventType.WORKBOOK_UPDATE,
        # ``data`` carries opaque extras (comment bodies, mention targets);
        # stringify id-shaped keys so a nested snowflake cannot leak as a
        # JSON number. ``revision`` and other non-id numerics pass through.
        stringify_snowflake_ids(payload),
        user_ids=user_ids,
        exclude_user_ids=exclude_user_ids,
    )


# --------------------------------------------------------------------------
# Security / account alerts (bridges the audit log to live clients)
# --------------------------------------------------------------------------
@_non_raising
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
            "user_id": str(user_id),
            "action": action,
            "detail": stringify_snowflake_ids(detail or {}),
        },
        user_ids=[user_id],
    )
