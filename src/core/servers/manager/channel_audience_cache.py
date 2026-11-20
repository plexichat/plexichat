"""Bounded cache of the server-member id lists used by message fanout.

Why this exists
---------------
Message events (``MESSAGE_CREATE`` / ``MESSAGE_UPDATE`` / ``MESSAGE_DELETE``)
resolve their audience in ``broadcast._resolve_recipients``. For a server
channel that resolution lists every member of the server with::

    SELECT user_id FROM srv_members WHERE server_id = ?

and then filters that list by ``channels.view``. On a busy channel a burst of
sends re-runs the *same* member listing for every single message even though
the roster only changes on a membership write (join / leave / kick / ban /
server delete).

This module memoizes just that member-id list in the shared, process-local
:class:`~src.core.bounded_lru.BoundedLRU` primitive. It is deliberately
conservative:

* **Bounded** -- hard entry/byte ceilings plus a short monotonic TTL; a stream
  of distinct server ids cannot grow it without limit and a roster can never
  be served past the TTL window.
* **Invalidated on every membership mutation** -- the shared membership-cache
  funnel calls :func:`invalidate_channel_audience_cache`, so a removed member
  is never broadcast to and a new member is included immediately.
* **Keyed by the audience source's identity and the server id** -- re-setting
  the servers module or resolving a different server produces a different key,
  so one server's roster can never be served for another's.
* **Copy-safe** -- values are returned by value; a caller mutating the list it
  receives cannot corrupt a later reuse of the cached roster.

Only the member list is cached, never the ``channels.view`` decision. The
per-member permission check still runs on every broadcast, so a role/override
change takes effect immediately without needing its own invalidation path.
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.core.bounded_lru import BoundedLRU

#: Rosters are cheap to recompute from ``srv_members``; keep the TTL short
#: enough that a write from another worker becomes visible quickly even if its
#: invalidation did not reach this process.
CHANNEL_AUDIENCE_TTL_SECONDS = 10.0

#: Bounded well below any plausible server count; hot channels stay resident.
CHANNEL_AUDIENCE_MAX_ENTRIES = 512
CHANNEL_AUDIENCE_MAX_BYTES = 8 * 1024 * 1024


def _member_list_size(value: Any) -> int:
    """Cheap byte estimate for a roster list (no ``repr`` of a huge list)."""
    try:
        return len(value) * 16 + 64
    except TypeError:
        return 64


_channel_audience_cache = BoundedLRU(
    max_entries=CHANNEL_AUDIENCE_MAX_ENTRIES,
    max_bytes=CHANNEL_AUDIENCE_MAX_BYTES,
    ttl_seconds=CHANNEL_AUDIENCE_TTL_SECONDS,
    size_of=_member_list_size,
)


def get_cached_channel_members(servers_mod: Any, server_id: Any) -> List[Any]:
    """Return the member user-id list for ``server_id``, cached on the hot path.

    A cache hit performs no backend query. A miss always recomputes from the
    authoritative ``get_member_user_ids`` source and stores a defensive copy.
    A failed lookup is cached as an empty list: for a broadcast audience the
    safe direction is to withhold an event for one TTL window, never to widen
    it. ``servers_mod`` participates in the key by identity so swapping the
    audience source (module re-setup, test fake) forces a fresh resolution.
    """
    try:
        scope = int(server_id)
    except (TypeError, ValueError):
        scope = server_id
    key = (servers_mod, scope)

    cached = _channel_audience_cache.get(key)
    if cached is not None:
        return list(cached)

    try:
        members = servers_mod.get_member_user_ids(server_id) or []
    except Exception:
        members = []
    stored = list(members)
    _channel_audience_cache.set(key, stored)
    return list(stored)


def invalidate_channel_audience_cache(server_id: Optional[Any] = None) -> None:
    """Drop cached rosters after a membership write.

    ``BoundedLRU`` supports full invalidation rather than per-key deletion.
    Membership mutations are rare compared with message sends, so clearing the
    small namespace is the conservative choice: no roster for a mutated server
    survives and the next broadcast recomputes it from ``srv_members``.
    ``server_id`` is accepted for call-site clarity; the whole namespace is
    cleared because the primitive has no targeted delete.
    """
    _channel_audience_cache.clear()


def channel_audience_cache_stats() -> dict:
    """Counters and bound snapshot for regression tests and tooling."""
    return _channel_audience_cache.stats()


__all__ = [
    "get_cached_channel_members",
    "invalidate_channel_audience_cache",
    "channel_audience_cache_stats",
    "CHANNEL_AUDIENCE_TTL_SECONDS",
    "CHANNEL_AUDIENCE_MAX_ENTRIES",
    "CHANNEL_AUDIENCE_MAX_BYTES",
]
