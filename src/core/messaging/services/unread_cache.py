"""Bounded, process-local cache for messaging unread-count aggregation.

A single API "sync" burst reads unread counts several times for the same user:

* ``GET /users/@me/unread`` aggregates counts for every conversation, and
* ``GET /channels/{id}/messages/unread`` asks for one channel at a time.

Before this cache each of those calls ran its own COUNT / GROUP BY over
``msg_participants`` joined with ``msg_messages`` (plus a participation probe
for the single-conversation form). The same data was recomputed once per read.

The entries live in the shared bounded in-process cache tier
(``src.core.database.cache.base._mem_cache``: at most 1000 keys / 8 MiB), so
this cache cannot grow without bound. The TTL is deliberately short: it is
only a safety net for writes that bypass the messaging service. Every read
state transition (mark read / bulk ack / mark unread) invalidates the calling
user's entries, and every message write invalidates the whole namespace.
"""

from typing import Dict, Optional

from src.core.base import SnowflakeID
from src.core.database.cache.base import (
    mem_cache_clear_pattern,
    mem_cache_get,
    mem_cache_set,
)

_PREFIX = "unreadagg"

# Short safety-net TTL (seconds). Normal staleness is removed eagerly by the
# invalidation calls in MessageStatusService and in the message send path.
_TTL_SECONDS = 2


def _key(user_id: SnowflakeID) -> str:
    """Stable, type-normalized user key (DB rows use ints, tokens often str)."""
    try:
        return str(int(user_id))
    except (TypeError, ValueError):
        return str(user_id)


def _all_key(user_id: SnowflakeID) -> str:
    return f"{_PREFIX}:all:{_key(user_id)}"


def _one_key(user_id: SnowflakeID, conversation_id: SnowflakeID) -> str:
    return f"{_PREFIX}:one:{_key(user_id)}:{_key(conversation_id)}"


def get_all(user_id: SnowflakeID) -> Optional[Dict[SnowflakeID, int]]:
    """Return the cached conversation -> unread-count map, or ``None``."""
    value = mem_cache_get(_all_key(user_id))
    # Copy: callers mutate the returned dict (e.g. str-key it for JSON).
    return dict(value) if isinstance(value, dict) else None


def put_all(user_id: SnowflakeID, counts: Dict[SnowflakeID, int]) -> None:
    """Cache the aggregate map.

    Per-conversation entries are only materialized when a single-conversation
    read actually asks for them (see ``put_one``), so a user in thousands of
    conversations does not pay thousands of cache writes up front.
    """
    mem_cache_set(_all_key(user_id), dict(counts), _TTL_SECONDS)


def get_one(user_id: SnowflakeID, conversation_id: SnowflakeID) -> Optional[int]:
    """Return a cached single-conversation count, or ``None`` on a miss.

    ``None`` is reserved for a miss; a real zero is cached as the int ``0``.
    """
    value = mem_cache_get(_one_key(user_id, conversation_id))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def put_one(user_id: SnowflakeID, conversation_id: SnowflakeID, count: int) -> None:
    """Cache one conversation's unread count."""
    mem_cache_set(_one_key(user_id, conversation_id), int(count), _TTL_SECONDS)


def invalidate_user(user_id: SnowflakeID) -> None:
    """Drop every cached aggregate for one user (read-state changed)."""
    mem_cache_clear_pattern(_all_key(user_id))
    mem_cache_clear_pattern(f"{_PREFIX}:one:{_key(user_id)}:*")


def invalidate_all() -> None:
    """Drop every cached aggregate (a message was written to a conversation)."""
    mem_cache_clear_pattern(f"{_PREFIX}:*")
