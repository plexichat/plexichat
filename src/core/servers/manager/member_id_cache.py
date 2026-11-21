"""Bounded, short-TTL cache for raw server member-id lists.

Why this exists
---------------
``MemberOpsMixin.get_member_user_ids`` answers ``SELECT user_id FROM
srv_members WHERE server_id = ?``.  It is a hot fan-out primitive: gateway
event routing, presence/typing audiences, voice, websocket handlers, emoji and
sticker listings, suite hydration and the REST friend/mention paths all call
it.  The roster only changes on a membership write (join / leave / kick / ban /
temporary reaping), so a burst of events for one server re-issued the identical
query on every event.

This module memoizes just that id list in a bounded, process-local
:class:`~src.core.bounded_lru.BoundedLRU` primitive.  Safety properties:

* **Bounded** -- hard entry/byte ceilings plus a short monotonic TTL.  A flood
  of distinct server ids cannot grow it without bound, and a roster can never
  be served past the TTL window (the backstop for a write from another worker).
* **Keyed by the complete answer identity** -- ``(server_id, exclude_user_id)``.
  The exclude filter is part of the key, so a list for one server or one
  exclusion can never be replayed for another.  No credential/token is stored.
* **Fail closed** -- only explicitly loaded lists are cached.  A miss, an
  expired entry, or any cache error returns the ``_MISS`` sentinel so the
  caller recomputes from the authoritative ``srv_members`` table.  The cache
  never invents or widens an audience.
* **Copy in / copy out** -- values are stored and returned as fresh lists, so a
  caller that sorts/mutates its result cannot poison a later fan-out.

This is a process-local L0 accelerator; a membership write in another worker
becomes visible within ``MEMBER_ID_TTL_SECONDS`` even if its invalidation did
not reach this process.  Local writes clear the namespace immediately through
the shared membership-cache funnel (``invalidate_member_count_cache``).
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.core.bounded_lru import BoundedLRU

#: Rosters are cheap to recompute; keep the cross-worker staleness window in
#: line with the other process-local fan-out caches (10s).
MEMBER_ID_TTL_SECONDS = 10.0
MEMBER_ID_MAX_ENTRIES = 512
MEMBER_ID_MAX_BYTES = 4 * 1024 * 1024

#: Distinct from any possible cached value (always a list) so a cached empty
#: roster is a real hit rather than being mistaken for a miss.
_MISS = object()

_Key = tuple[int, Optional[int]]


def _member_id_list_size(value: Any) -> int:
    """Cheap byte estimate for an id list (no ``repr`` of a huge roster)."""
    try:
        return len(value) * 16 + 64
    except TypeError:
        return 64


_member_id_cache = BoundedLRU(
    max_entries=MEMBER_ID_MAX_ENTRIES,
    max_bytes=MEMBER_ID_MAX_BYTES,
    ttl_seconds=MEMBER_ID_TTL_SECONDS,
    size_of=_member_id_list_size,
)


def get_cached_member_user_ids(server_id: Any, exclude_user_id: Any = None) -> Any:
    """Return a copy of the cached roster, or ``_MISS`` on a miss/error."""
    try:
        key: _Key = (
            int(server_id),
            None if exclude_user_id is None else int(exclude_user_id),
        )
        value = _member_id_cache.get(key, _MISS)
    except Exception:  # pragma: no cover - defensive; never widen an audience
        return _MISS
    if value is _MISS or not isinstance(value, list):
        # Never replay a malformed entry: a non-list (or a corrupt cache
        # payload) is a miss, so the caller recomputes from the authoritative
        # table instead of widening the audience with garbage.
        return _MISS
    return list(value)


def prime_member_user_ids(
    server_id: Any, exclude_user_id: Any, user_ids: List[Any]
) -> None:
    """Cache a defensive copy of an authoritative member-id list."""
    try:
        key: _Key = (
            int(server_id),
            None if exclude_user_id is None else int(exclude_user_id),
        )
        _member_id_cache.set(key, list(user_ids))
    except Exception:  # pragma: no cover - cache is an accelerator only
        pass


def invalidate_member_id_cache() -> None:
    """Drop every cached roster after a membership write.

    Membership mutations are rare compared with event fan-out, so clearing the
    small bounded namespace is the conservative choice: no roster for a
    mutated server survives, and the next read recomputes it from
    ``srv_members``.
    """
    _member_id_cache.clear()


def member_id_cache_stats() -> dict:
    """Counters and bound snapshot for regression tests and tooling."""
    return _member_id_cache.stats()


__all__ = [
    "get_cached_member_user_ids",
    "prime_member_user_ids",
    "invalidate_member_id_cache",
    "member_id_cache_stats",
    "MEMBER_ID_TTL_SECONDS",
    "MEMBER_ID_MAX_ENTRIES",
    "MEMBER_ID_MAX_BYTES",
    "_MISS",
]
