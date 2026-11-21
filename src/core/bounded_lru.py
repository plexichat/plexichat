"""
Thread-safe bounded LRU cache with entry-count, byte-size and TTL eviction.

Process-local caches keyed by request-derived data must stay bounded no matter
how many distinct keys traffic generates. This primitive enforces all three
limits on every write, so a cache can never become an unbounded in-process
registry:

* ``max_entries`` caps the number of live keys (LRU eviction on overflow);
* ``max_bytes`` caps the approximate footprint of the cached values;
* ``ttl_seconds`` bounds staleness -- expired entries are never served and are
  dropped on access, so a revoked/rotated decision cannot live past the TTL.

Expired values are reclaimed with a min-heap keyed by expiry, so a write does
not have to walk every entry to find them. The heap is compacted when it grows
past twice the entry cap, keeping its memory bounded and the amortised cost of
a write O(log n) instead of O(n) -- important because a bounded cache at its
steady state breaches the entry cap on *every* insert.

``clear()`` is a full invalidation hook: callers that change the underlying
source of truth (a license hot-swap, a config reload, ...) must call it so no
stale verdict survives. Eviction is always safe for a pure-computation cache:
a missing entry is recomputed from the authoritative source, never assumed.
"""

import heapq
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional


def value_size(value: Any) -> int:
    """Cheap conservative byte estimate for a cached value.

    Strings/bytes are measured exactly; everything else falls back to a
    repr-based estimate. Callers holding large structured values can pass a
    more accurate ``size_of`` to :class:`BoundedLRU`.
    """
    if isinstance(value, str):
        return len(value.encode("utf-8", "ignore"))
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    return len(repr(value)) + 64


class BoundedLRU:
    """A dict-like LRU with hard entry, byte and TTL bounds.

    Stores ``key -> (expires_at, value, size)`` in an ``OrderedDict`` kept in
    least-recently-used order. All mutations run under one re-entrant lock so
    the counters and the byte total stay consistent under concurrent traffic.
    """

    def __init__(
        self,
        max_entries: int,
        max_bytes: int,
        ttl_seconds: float,
        size_of: Optional[Callable[[Any], int]] = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("BoundedLRU max_entries must be at least 1")
        if max_bytes < 1:
            raise ValueError("BoundedLRU max_bytes must be at least 1")
        if ttl_seconds <= 0:
            raise ValueError("BoundedLRU ttl_seconds must be positive")
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._ttl = float(ttl_seconds)
        self._size_of = size_of or value_size
        self._data: "OrderedDict[Any, tuple[float, Any, int]]" = OrderedDict()
        self._bytes = 0
        # Min-heap of (expires_at, sequence, key). A stale heap entry is one
        # whose key was re-set (different expiry) or removed; ``_drop_expired``
        # ignores it by checking the live entry's expiry. Sequence breaks ties
        # without comparing (possibly unorderable) keys.
        self._expiry_heap: "list[tuple[float, int, Any]]" = []
        self._seq = 0
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def get(self, key: Any, default: Any = None) -> Any:
        """Return a live value, refreshing recency, or ``default`` on miss.

        An expired entry is removed and treated as a miss. This is the guard
        against serving stale security/auth decisions: callers recompute from
        the authoritative source whenever ``get`` returns the default.
        """
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return default
            expires_at, value, size = item
            if expires_at <= now:
                self._data.pop(key, None)
                self._bytes -= size
                self._expirations += 1
                self._misses += 1
                return default
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def _drop_expired(self, now: float) -> None:
        """Reclaim entries whose TTL elapsed, using the min-heap index.

        Caller must hold ``self._lock``. Only entries that are actually expired
        are visited (amortised O(log n) each), unlike the old full ``items()``
        snapshot scan which was O(n) on every write at capacity. Stale heap
        entries are discarded; they never evict a live value.
        """
        heap = self._expiry_heap
        while heap and heap[0][0] <= now:
            exp, _seq, key = heapq.heappop(heap)
            item = self._data.get(key)
            if item is not None and item[0] == exp:
                self._data.pop(key, None)
                self._bytes -= item[2]
                self._expirations += 1

    def _compact_heap(self) -> None:
        """Rebuild the heap from live entries; cap stale-entry accumulation.

        Caller must hold ``self._lock``. Amortised to one O(n) rebuild per
        ``_max_entries`` writes, so steady-state writes stay O(log n).
        """
        self._expiry_heap = [
            (item[0], i, key) for i, (key, item) in enumerate(self._data.items())
        ]
        heapq.heapify(self._expiry_heap)
        self._seq = len(self._expiry_heap)

    def set(self, key: Any, value: Any) -> None:
        """Store ``value``, evicting expired then least-recently-used entries.

        A single value larger than ``max_bytes`` is not cached at all: dropping
        it (rather than keeping an over-budget entry) keeps the byte ceiling
        hard, and the caller simply recomputes on the next miss.
        """
        now = time.monotonic()
        expires_at = now + self._ttl
        size = max(0, int(self._size_of(value)))
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old[2]
            self._data[key] = (expires_at, value, size)
            self._bytes += size
            heapq.heappush(self._expiry_heap, (expires_at, self._seq, key))
            self._seq += 1

            # Keep stale heap entries (from re-sets, LRU evictions and
            # ``get``-time expiry) from growing without bound.
            if len(self._expiry_heap) > 2 * self._max_entries:
                self._compact_heap()

            # Reclaim expired keys before evicting live LRU entries. Only pay
            # this cost when a bound is breached: with spare capacity a
            # stale-but-unread key is harmless -- ``get`` drops it on access
            # and never serves it, so fail-closed TTL semantics are unchanged.
            if len(self._data) > self._max_entries or self._bytes > self._max_bytes:
                self._drop_expired(now)

            while self._data and (
                len(self._data) > self._max_entries or self._bytes > self._max_bytes
            ):
                _evicted_key, (_exp, _v, sz) = self._data.popitem(last=False)
                self._bytes -= sz
                self._evictions += 1

            # Oversized single value: it survived only by evicting everything
            # else and may still breach the byte cap. Drop it outright.
            if self._data and self._bytes > self._max_bytes:
                _k, (_exp, _v, sz) = self._data.popitem(last=False)
                self._bytes -= sz
                self._evictions += 1

    def clear(self) -> None:
        """Full invalidation: drop every entry (counters are preserved)."""
        with self._lock:
            self._data.clear()
            self._bytes = 0
            self._expiry_heap.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: Any) -> bool:
        """True only for a live (non-expired) entry; expired ones are dropped.

        Unlike ``get`` this does not refresh recency or touch hit/miss counters.
        """
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return False
            if item[0] <= now:
                self._data.pop(key, None)
                self._bytes -= item[2]
                self._expirations += 1
                return False
            return True

    def stats(self) -> dict:
        """Snapshot of counters and current usage (useful in tests/tools)."""
        with self._lock:
            return {
                "size": len(self._data),
                "bytes": self._bytes,
                "max_entries": self._max_entries,
                "max_bytes": self._max_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "expirations": self._expirations,
            }
