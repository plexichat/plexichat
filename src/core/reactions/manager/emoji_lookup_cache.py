"""Bounded, short-TTL cache for core custom-emoji catalog/list resolution.

The HTTP layer memoizes the *responses* for ``GET /servers/{id}/emojis`` and the
single-emoji detail route with credential-partitioned keys, but several core
callers resolve the same emoji catalog directly through ``ReactionManager``:

* reaction-dispatch helpers re-read the server emoji list to build the
  ``GUILD_EMOJIS_UPDATE`` payload after every mutation;
* message/embed enrichment and direct module-API callers ask for the same list
  or the same single emoji again within one request burst;
* ``get_emoji_counts`` runs two ``COUNT(*)`` queries and is not cached at all
  at the HTTP layer.

Each of those resolutions repeats a ``react_custom_emoji`` query (the list even
JOINs ``auth_users`` for the uploader name).  This module memoizes those *query
results* in-process for a very short window so a burst of reads on the same
server pays for the query once.

Safety properties:

* **Bounded** -- hard ``max_entries`` and ``max_bytes`` caps with LRU eviction;
  a hostile stream of distinct emoji/server ids cannot grow it without limit.
* **Short TTL** -- entries expire on a monotonic clock and expired values are
  never served, which bounds cross-worker staleness.
* **Targeted invalidation** -- every custom-emoji create/update/delete drops the
  affected server's list/count entries and the affected emoji's detail entry.
  Callers that write rows outside the manager can call ``clear()``.
* **Server-scoped keys, no credential segment** -- the catalog is shared server
  data, not per-user data, so two credentials legitimately share the same
  cached *query result*.  Authorization stays outside this tier: the HTTP layer
  runs its membership/permission gate before the response cache and keeps the
  credential-partitioned keys.  Caching here never bypasses a gate.

This is an in-process L0 tier.  It is deliberately tiny and local; the
distributed response cache in the API layer keeps its own bounded L1/L2 tiers.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable, Tuple

#: A few seconds is enough to coalesce one request burst without replacing the
#: source of truth for cross-process writers.
DEFAULT_EMOJI_TTL_SECONDS = 5.0

#: Generous for the number of servers/emojis a single worker touches in a burst,
#: still a hard ceiling against key flooding.
DEFAULT_EMOJI_MAX_ENTRIES = 256

#: ~256 KiB of cached list/detail/count payloads per worker.
DEFAULT_EMOJI_MAX_BYTES = 256 * 1024

Key = Tuple[Any, ...]
_MISS = object()


def _value_size(value: Any) -> int:
    """Cheap, conservative footprint estimate for cached emoji payloads."""
    if isinstance(value, str):
        return len(value.encode("utf-8", "ignore"))
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, (list, tuple, set)):
        return sum(_value_size(item) for item in value) + 64
    if isinstance(value, dict):
        return sum(_value_size(k) + _value_size(v) for k, v in value.items()) + 64
    return len(repr(value)) + 64


class EmojiCatalogCache:
    """A thread-safe bounded LRU with a short monotonic TTL.

    ``clock`` is injectable so tests can advance time without sleeping.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_EMOJI_TTL_SECONDS,
        max_entries: int = DEFAULT_EMOJI_MAX_ENTRIES,
        max_bytes: int = DEFAULT_EMOJI_MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._max_bytes = max(1, int(max_bytes))
        self._clock = clock
        self._data: "OrderedDict[Key, tuple[float, Any, int]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0

    def get(self, key: Key, default: Any = _MISS) -> Any:
        """Return a live value or ``default``; expired entries are dropped."""
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return default
            expires_at, value, size = item
            if expires_at <= now:
                del self._data[key]
                self._bytes -= size
                self._expirations += 1
                self._misses += 1
                return default
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: Key, value: Any) -> None:
        """Store ``value``, evicting expired/LRU entries to stay under the caps."""
        now = self._clock()
        size = max(0, int(_value_size(value)))
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old[2]
            self._data[key] = (now + self._ttl, value, size)
            self._bytes += size

            # Sweeping is O(n) and allocates a snapshot of every entry, so only
            # pay it when a bound is breached: with spare capacity an expired
            # key is harmless because ``get`` drops it and never serves it.
            if len(self._data) > self._max_entries or self._bytes > self._max_bytes:
                for stale_key, (exp, _v, sz) in list(self._data.items()):
                    if exp <= now:
                        del self._data[stale_key]
                        self._bytes -= sz
                        self._expirations += 1

            while self._data and (
                len(self._data) > self._max_entries or self._bytes > self._max_bytes
            ):
                _evicted, (_exp, _v, sz) = self._data.popitem(last=False)
                self._bytes -= sz
                self._evictions += 1

            if self._data and self._bytes > self._max_bytes:
                _k, (_exp, _v, sz) = self._data.popitem(last=False)
                self._bytes -= sz
                self._evictions += 1

    def invalidate_server(self, server_id: Hashable) -> None:
        """Drop every list/count entry for one server."""
        with self._lock:
            for key in [
                k
                for k in self._data
                if k[0] in ("list", "counts") and k[1] == server_id
            ]:
                self._drop(key)

    def invalidate_emoji(self, emoji_id: Hashable) -> None:
        """Drop the cached detail entry for one emoji."""
        with self._lock:
            self._drop(("detail", emoji_id))

    def clear(self) -> None:
        """Full invalidation hook for out-of-band writers/tests."""
        with self._lock:
            self._data.clear()
            self._bytes = 0

    def stats(self) -> dict:
        """Counters for the regression tests and observability."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "expirations": self._expirations,
                "size": len(self._data),
                "bytes": self._bytes,
                "max_entries": self._max_entries,
                "max_bytes": self._max_bytes,
                "ttl_seconds": self._ttl,
            }

    def _drop(self, key: Key) -> None:
        item = self._data.pop(key, None)
        if item is not None:
            self._bytes -= item[2]


__all__ = [
    "EmojiCatalogCache",
    "DEFAULT_EMOJI_TTL_SECONDS",
    "DEFAULT_EMOJI_MAX_ENTRIES",
    "DEFAULT_EMOJI_MAX_BYTES",
]
