"""Bounded, short-TTL cache for the member counts used by server listings.

``ServerOpsMixin.get_servers`` hydrates every server in a user's list with a
``member_count``.  Historically that value was a correlated scalar subquery::

    (SELECT COUNT(*) FROM srv_members WHERE server_id = s.id) as member_count

The same servers appear in many different users' listings and in every refresh
of one user's list, so a burst of listing requests re-ran the same per-server
``COUNT`` over and over even though the count only changes on a membership
write (join / leave / kick / ban / temporary reaping).

This module memoizes the per-server integer count in the shared, process-local
:class:`~src.core.bounded_lru.BoundedLRU` primitive.  Safety properties:

* **Bounded** -- hard ``max_entries`` / ``max_bytes`` ceilings plus a short
  monotonic TTL; a hostile stream of distinct server ids cannot grow it
  without bound and a count can never be served past the TTL window.
* **Invalidated on every membership mutation** -- the membership-lifecycle
  helper clears the namespace, so a removed member is never counted after a
  leave and a new member is counted immediately after a join.
* **Misses recompute** -- a cached miss is always resolved from the
  authoritative ``srv_members`` table, never assumed. Only final integers are
  stored (no viewer-specific state), so all callers can share one entry.

The cache is an in-process L0 accelerator; a write in another worker is bounded
by ``ttl_seconds``, exactly like the surrounding ``get_servers`` cache tier.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.core.bounded_lru import BoundedLRU

#: Counts are cheap to recompute and must not outlive the surrounding 15s
#: ``get_servers`` cache by much, so keep this window deliberately short.
MEMBER_COUNT_TTL_SECONDS = 10.0
MEMBER_COUNT_MAX_ENTRIES = 4096
MEMBER_COUNT_MAX_BYTES = 1 * 1024 * 1024

# Keep each batched IN (...) clause comfortably below SQLite/PG variable caps.
_BATCH_CHUNK = 500

_member_count_cache = BoundedLRU(
    max_entries=MEMBER_COUNT_MAX_ENTRIES,
    max_bytes=MEMBER_COUNT_MAX_BYTES,
    ttl_seconds=MEMBER_COUNT_TTL_SECONDS,
)


def get_cached_member_count(server_id: Any) -> Optional[int]:
    """Return a live cached count for *server_id*, or ``None`` on a miss."""
    return _member_count_cache.get(int(server_id))


def prime_member_counts(db: Any, server_ids: List[Any]) -> Dict[int, int]:
    """Return ``{server_id: member_count}`` for *server_ids* using the cache.

    Ids already cached are served from memory. The remaining ids are resolved
    in one batched ``GROUP BY`` query (chunked to stay under the DB variable
    limit) and stored so later listings reuse them. A miss is always recomputed
    from the authoritative table.
    """
    ids = [int(sid) for sid in server_ids]
    if not ids:
        return {}

    counts: Dict[int, int] = {}
    missing: List[int] = []
    seen = set()
    for sid in ids:
        if sid in seen:
            continue
        seen.add(sid)
        cached = _member_count_cache.get(sid)
        if cached is None:
            missing.append(sid)
        else:
            counts[sid] = cached

    for start in range(0, len(missing), _BATCH_CHUNK):
        chunk = missing[start : start + _BATCH_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "SELECT server_id, COUNT(*) AS c FROM srv_members "
            f"WHERE server_id IN ({placeholders}) GROUP BY server_id"
        )
        rows = db.fetch_all(query, tuple(chunk)) or []
        found = {int(row["server_id"]): int(row["c"]) for row in rows}
        for sid in chunk:
            count = found.get(sid, 0)
            counts[sid] = count
            _member_count_cache.set(sid, count)

    return counts


def invalidate_member_count_cache() -> None:
    """Drop every membership-derived cache after a membership write.

    ``BoundedLRU`` exposes full invalidation (``clear``) rather than a per-key
    delete. A membership mutation is rare compared with listing/fanout reads,
    so clearing these small namespaces is the conservative choice: no value
    for a mutated server can survive and the next read recomputes it from the
    authoritative table.

    This helper is the shared funnel called by every join/kick/ban/leave
    and by server deletion (``CacheOpsMixin._invalidate_member_caches`` and
    ``ServerOpsMixin.delete_server``). The message-fanout member listing is
    derived from the same ``srv_members`` rows, so clear its cache in the same
    funnel: a removed member must not keep receiving message events and a new
    member must be included, both immediately after the write.
    """
    _member_count_cache.clear()
    from .channel_audience_cache import invalidate_channel_audience_cache
    from .member_id_cache import invalidate_member_id_cache
    from .shared_member_cache import invalidate_shared_member_cache

    invalidate_channel_audience_cache()
    # The raw member-id list is the same ``srv_members`` projection used by
    # gateway/presence/voice fan-out. A join/kick/ban/leave must evict it in the
    # same funnel so no removed member keeps receiving events and no new member
    # is left out for the cache TTL.
    invalidate_member_id_cache()
    # The shared-server audience is derived from the same rows.  A membership
    # write must evict it too, or a removed member keeps receiving presence /
    # typing fan-out and an added member stays invisible until the TTL expires.
    invalidate_shared_member_cache()


def member_count_cache_stats() -> dict:
    """Counters and bound snapshot for regression tests and tooling."""
    return _member_count_cache.stats()


__all__ = [
    "get_cached_member_count",
    "prime_member_counts",
    "invalidate_member_count_cache",
    "member_count_cache_stats",
    "MEMBER_COUNT_TTL_SECONDS",
    "MEMBER_COUNT_MAX_ENTRIES",
    "MEMBER_COUNT_MAX_BYTES",
]
