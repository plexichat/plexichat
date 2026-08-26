"""
Shared builders for MESSAGE_CREATE / MESSAGE_UPDATE / MESSAGE_DELETE
gateway payloads (the flat ``MessageResponse``-shaped family).

Every producer of live message events (the messages CRUD BroadcastMixin,
AutoMod actions, artifact transcript messages) MUST build its event
through this module so that:

- ID fields are always strings on the wire,
- the canonical server scope key is ``server_id`` (with the legacy
  ``guild_id`` alias emitted for older clients), and
- routing/intent metadata on the :class:`Event` itself stays consistent.

Wire shapes are otherwise unchanged; these helpers never invent fields.
"""

import time
from typing import Any, Dict, Optional

from .models import Event
from .types import EventType


def _str(value: Any) -> Optional[str]:
    """Stringify an ID for JSON serialization (None stays None)."""
    return str(value) if value is not None else None


def apply_server_scope(
    data: Dict[str, Any], server_id: Optional[int]
) -> Dict[str, Any]:
    """Stamp the server scope onto a message payload.

    ``server_id`` is canonical; ``guild_id`` is emitted as an exact alias
    because existing client readers still look for either name. DM/group
    messages (no server) carry explicit nulls so clients can rely on the
    keys being present.
    """
    sid = _str(server_id)
    data["server_id"] = sid
    data["guild_id"] = sid
    return data


def build_message_create(
    data: Dict[str, Any],
    *,
    channel_id: Optional[int],
    server_id: Optional[int] = None,
) -> Event:
    """Build a MESSAGE_CREATE event from a flat message payload dict."""
    return Event(
        event_type=EventType.MESSAGE_CREATE,
        data=apply_server_scope(dict(data), server_id),
        server_id=server_id,
        channel_id=channel_id,
    )


def build_message_update(
    data: Dict[str, Any],
    *,
    channel_id: Optional[int],
    server_id: Optional[int] = None,
) -> Event:
    """Build a MESSAGE_UPDATE event from a flat message payload dict."""
    return Event(
        event_type=EventType.MESSAGE_UPDATE,
        data=apply_server_scope(dict(data), server_id),
        server_id=server_id,
        channel_id=channel_id,
    )


def build_message_delete(
    message_id: Any,
    channel_id: Optional[int],
    *,
    server_id: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Event:
    """Build a MESSAGE_DELETE tombstone event.

    ``extra`` carries optional markers (e.g. ``{"automod": True}`` for
    hard-delete moderation removals).
    """
    data: Dict[str, Any] = {
        "id": _str(message_id),
        "channel_id": _str(channel_id),
    }
    apply_server_scope(data, server_id)
    if extra:
        data.update(extra)
    return Event(
        event_type=EventType.MESSAGE_DELETE,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
    )


def build_flat_message_create(
    *,
    message_id: Any,
    channel_id: Optional[int],
    author_id: Any,
    content: str,
    author_username: Optional[str] = None,
    author_avatar_url: Optional[str] = None,
    created_at: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    mentions: Optional[list] = None,
    server_id: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Event:
    """Build a MESSAGE_CREATE event in the FLAT family.

    This is the shape every live client consumes (``author_id``,
    ``author_username``, ``created_at``, ...). Use it instead of the
    Discord-style :func:`payloads.create_message_create` whenever the
    event must be rendered by the web client — that factory nests the
    author under ``author.id`` and timestamps it differently, which own-
    message detection and renderers cannot read.
    """
    aid = _str(author_id)
    data: Dict[str, Any] = {
        "id": _str(message_id),
        "channel_id": _str(channel_id),
        "author_id": aid,
        "content": content,
        "created_at": int(created_at)
        if created_at is not None
        else int(time.time() * 1000),
        "edited_at": None,
        "deleted": False,
        "reply_to_id": None,
        "attachments": [],
        "embeds": [],
        "pinned": False,
        "mentions": list(mentions or []),
        "author_username": author_username or f"User {aid}",
        "author_avatar_url": author_avatar_url,
        "reactions": [],
        "metadata": dict(metadata) if metadata else None,
    }
    if extra:
        data.update(extra)
    return build_message_create(data, channel_id=channel_id, server_id=server_id)
