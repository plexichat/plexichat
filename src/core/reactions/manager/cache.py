"""Bounded, short-TTL cache for hot reaction aggregation reads.

The reaction manager's read paths repeat two expensive queries on every message
view:

* a ``GROUP BY`` over ``react_reactions`` with a per-viewer ``me`` flag and a
  bidirectional ``rel_blocked`` filter (``get_reactions_batch``), and
* ``COUNT(*)`` aggregates that apply the same per-viewer block filter
  (``get_reaction_count`` / ``get_reaction_count_by_emoji``) plus paginated
  reactor lookups (``get_reaction_users``).

The HTTP layer memoizes only the *single-message* ``GET .../reactions`` body;
message-list hydration, delete/automod enrichment and direct core callers still
hit the aggregation queries on every request.  This module memoizes the core
query results themselves for a very short window.

Safety properties (see ``src/tests/reactions/test_reaction_read_cache_core.py``):

* **Bounded** -- at most ``max_entries`` live entries; the least-recently-used
  entry is evicted on insert.  A hostile stream of distinct message ids cannot
  grow the cache without limit.
* **Short TTL** -- entries expire after a few seconds, bounding cross-worker
  staleness when a write happens on another process.
* **Per-viewer / per-credential keys** -- the viewer id is part of every key,
  and callers that know the credential (user vs. bot token) can pass an extra
  discriminator.  Blocked-user filtering and the ``me`` flag are viewer
  specific, so two viewers can never read each other's cached body.
* **Gate before cache** -- the cache stores *query results only*.  Callers run
  the live message/participant authorization check before consulting the
  cache, so a revoked viewer is denied even while a warm entry exists.
* **Invalidated on every mutation** -- any reaction or custom-emoji write drops
  the namespace; a cached pill list cannot survive a write.

This is an in-process L0 tier.  It is deliberately tiny and local: the
distributed response cache lives in the HTTP layer and already has its own
bounded L1/L2 tiers and credential-aware keys.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Optional, Tuple

#: How long a cached aggregation result stays valid (seconds).  Short on
#: purpose: the window is only meant to coalesce repeated reads of the same
#: message list within one request burst, not to replace the source of truth.
DEFAULT_READ_TTL_SECONDS = 3.0

#: Hard cap on live entries.  With 100 message ids per batch and a handful of
#: viewers this is generous, but still bounded against key-flooding.
DEFAULT_MAX_ENTRIES = 512

Key = Tuple[Any, ...]

_MISS = object()


@dataclass(frozen=True)
class _Entry:
    value: Any
    expires_at: float


def reaction_read_key(
    namespace: str,
    viewer_id: Optional[int],
    *parts: Hashable,
    credential: Optional[str] = None,
) -> Key:
    """Build a hashable key partitioned by viewer and (optionally) credential.

    ``viewer_id`` is always included: the block filter and the ``me`` flag are
    functions of the viewer, so a cached result for one viewer is never valid
    for another.  ``credential`` lets an HTTP caller that knows the token kind
    (e.g. ``user:42`` vs ``bot:999:42``) keep those namespaces apart as well.
    Only hashable scalars are expected from callers.
    """
    return (namespace, credential or "", viewer_id or 0, *parts)


class ReactionReadCache:
    """A thread-safe bounded LRU with a short monotonic TTL.

    ``clock`` is injectable so tests can advance time without sleeping.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_READ_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._data: "OrderedDict[Key, _Entry]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: Key) -> Any:
        """Return a live value or ``_MISS``; refresh LRU order on a hit."""
        now = self._clock()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self._misses += 1
                return _MISS
            if entry.expires_at <= now:
                # Expired: drop it and treat as a miss.  Nothing is returned to
                # the caller, so stale data can never leak on a TTL boundary.
                del self._data[key]
                self._misses += 1
                return _MISS
            self._data.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, key: Key, value: Any) -> None:
        """Store ``value``, evicting the LRU entry when at capacity."""
        now = self._clock()
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            elif len(self._data) >= self._max_entries:
                self._data.popitem(last=False)
                self._evictions += 1
            self._data[key] = _Entry(value=value, expires_at=now + self._ttl)

    def clear(self) -> None:
        """Drop every entry (called by all reaction mutation paths)."""
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        """Counters for regression tests and observability."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "size": len(self._data),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
            }


__all__ = [
    "ReactionReadCache",
    "reaction_read_key",
    "DEFAULT_READ_TTL_SECONDS",
    "DEFAULT_MAX_ENTRIES",
]
