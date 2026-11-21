"""Bounded, short-TTL cache for shared-server member-id audiences.

Why this exists
---------------
``MemberOpsMixin.get_all_shared_member_ids`` answers the self-join
``SELECT DISTINCT m2.user_id FROM srv_members m1 JOIN srv_members m2 ...``.
It is the audience-resolution primitive behind presence broadcasts, the
presence-read authorization gate, typing bursts on disconnect and profile
visibility.  The result is the full set of users sharing at least one server
with a subject -- potentially large -- and it only changes on a membership
write (join / leave / kick / ban / temporary reaping).  Yet every presence
poll and every offline/online fan-out re-issued the identical join.

This module memoizes just that id list in a bounded, process-local
:class:`~src.core.bounded_lru.BoundedLRU` primitive.  Safety properties:

* **Bounded** -- hard entry/byte ceilings plus a short monotonic TTL.  A flood
  of distinct subject ids cannot grow it without bound, and an answer can never
  be served past the TTL window (the backstop for a write from another worker).
* **Keyed by the complete answer identity** -- the subject user id alone.  The
  query has no other input, so no two subjects can ever share an entry; a
  credential is never stored.  This is what prevents a cross-user audience leak.
* **Fail closed** -- only explicitly loaded lists are cached.  A miss, an
  expired/malformed entry, or any cache error returns the ``_MISS`` sentinel so
  the caller recomputes from the authoritative ``srv_members`` table.  The cache
  never invents or widens an audience; an authoritative lookup error propagates
  to the caller, which already denies/filters recipients on failure.
* **Copy in / copy out** -- values are stored and returned as fresh lists, so a
  caller that sorts/mutates its audience cannot poison a later fan-out.

This is a process-local L0 accelerator; a membership write in another worker
becomes visible within ``SHARED_MEMBER_TTL_SECONDS`` even if its invalidation
did not reach this process.  Local writes clear the namespace immediately
through the shared membership-cache funnel (``invalidate_member_count_cache``).
"""

from __future__ import annotations

from typing import Any, List

from src.core.bounded_lru import BoundedLRU

#: Audiences are cheap to recompute; keep the cross-worker staleness window in
#: line with the other process-local fan-out caches (10s).
SHARED_MEMBER_TTL_SECONDS = 10.0
SHARED_MEMBER_MAX_ENTRIES = 512
SHARED_MEMBER_MAX_BYTES = 4 * 1024 * 1024

#: Distinct from any possible cached value (always a list) so a cached empty
#: audience is a real hit rather than being mistaken for a miss.
_MISS = object()


def _id_list_size(value: Any) -> int:
    """Cheap byte estimate for an id list (no ``repr`` of a huge audience)."""
    try:
        return len(value) * 16 + 64
    except TypeError:
        return 64


_shared_member_cache = BoundedLRU(
    max_entries=SHARED_MEMBER_MAX_ENTRIES,
    max_bytes=SHARED_MEMBER_MAX_BYTES,
    ttl_seconds=SHARED_MEMBER_TTL_SECONDS,
    size_of=_id_list_size,
)


def get_cached_shared_member_ids(user_id: Any) -> Any:
    """Return a copy of the cached shared-member ids, or ``_MISS`` on miss/error."""
    try:
        value = _shared_member_cache.get(int(user_id), _MISS)
    except Exception:  # pragma: no cover - defensive; never widen an audience
        return _MISS
    if value is _MISS or not isinstance(value, list):
        # Never replay a malformed entry: a non-list (or a corrupt cache
        # payload) is a miss, so the caller recomputes from the authoritative
        # table instead of widening the audience with garbage.
        return _MISS
    return list(value)


def prime_shared_member_ids(user_id: Any, user_ids: List[Any]) -> None:
    """Cache a defensive copy of an authoritative shared-member id list."""
    try:
        _shared_member_cache.set(int(user_id), list(user_ids))
    except Exception:  # pragma: no cover - cache is an accelerator only
        pass


def invalidate_shared_member_cache() -> None:
    """Drop every cached audience after a membership write.

    Membership mutations are rare compared with event fan-out, so clearing the
    small bounded namespace is the conservative choice: no audience for a
    mutated server survives, and the next read recomputes it from
    ``srv_members``.
    """
    _shared_member_cache.clear()


def shared_member_cache_stats() -> dict:
    """Counters and bound snapshot for regression tests and tooling."""
    return _shared_member_cache.stats()


__all__ = [
    "get_cached_shared_member_ids",
    "prime_shared_member_ids",
    "invalidate_shared_member_cache",
    "shared_member_cache_stats",
    "SHARED_MEMBER_TTL_SECONDS",
    "SHARED_MEMBER_MAX_ENTRIES",
    "SHARED_MEMBER_MAX_BYTES",
    "_MISS",
]
