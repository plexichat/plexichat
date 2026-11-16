"""
Bounded memoization for expensive, deterministic media work.

Thumbnail generation and video transcoding are pure functions of the input
bytes plus a handful of options, yet repeated requests used to redo the whole
decode/encode. This module provides a small thread-safe LRU keyed by a SHA-256
content hash with two independent bounds:

* ``maxsize`` / ``max_bytes`` cap the retained results;
* ``max_inflight`` caps concurrent *computations* so a burst of identical
  requests cannot fan out into unbounded CPU work.

Per-key stripe locks coalesce concurrent identical requests: only one thread
computes a given key while the others wait and then read the cached result.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, Callable, Hashable, Optional

_MISSING = object()


def content_hash(data: bytes) -> str:
    """Stable cache key component for a byte payload."""
    return hashlib.sha256(data).hexdigest()


def _default_sizer(value: Any) -> int:
    try:
        return max(1, len(value))
    except TypeError:
        return 1


class ResultCache:
    """Thread-safe bounded LRU with in-flight coalescing."""

    def __init__(
        self,
        maxsize: int = 128,
        max_bytes: int = 64 * 1024 * 1024,
        sizer: Optional[Callable[[Any], int]] = None,
        max_inflight: int = 4,
        lock_stripes: int = 64,
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        self._max_bytes = max(1, int(max_bytes))
        self._sizer = sizer or _default_sizer
        self._cache: "OrderedDict[Hashable, Any]" = OrderedDict()
        self._sizes: dict = {}
        self._bytes = 0
        self._lock = threading.Lock()
        self._stripes = [threading.Lock() for _ in range(max(1, int(lock_stripes)))]
        self._inflight = threading.BoundedSemaphore(max(1, int(max_inflight)))
        self._stats = {
            "hits": 0,
            "misses": 0,
            "computes": 0,
            "evictions": 0,
            "not_cached": 0,
        }

    def stats(self) -> dict:
        """Return a snapshot of cache counters (for tests/observability)."""
        with self._lock:
            return dict(self._stats)

    def record_compute(self, count: int = 1) -> None:
        """Account for work computed by callers that fill the cache via put()."""
        with self._lock:
            self._stats["computes"] += max(0, int(count))

    def clear(self) -> None:
        """Drop all cached results and reset accounting (counters preserved)."""
        with self._lock:
            self._cache.clear()
            self._sizes.clear()
            self._bytes = 0

    def _lookup(self, key: Hashable) -> Any:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._stats["hits"] += 1
                return self._cache[key]
            self._stats["misses"] += 1
            return _MISSING

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return the cached value or ``default``."""
        value = self._lookup(key)
        return default if value is _MISSING else value

    def put(self, key: Hashable, value: Any) -> None:
        """Cache ``value`` unless it alone exceeds the byte budget."""
        size = max(1, int(self._sizer(value)))
        if size > self._max_bytes:
            with self._lock:
                self._stats["not_cached"] += 1
            return
        with self._lock:
            if key in self._cache:
                self._bytes -= self._sizes.pop(key, 0)
            self._cache[key] = value
            self._sizes[key] = size
            self._bytes += size
            self._cache.move_to_end(key)
            self._evict_locked()

    def _evict_locked(self) -> None:
        while self._cache and (
            len(self._cache) > self._maxsize or self._bytes > self._max_bytes
        ):
            key, _ = self._cache.popitem(last=False)
            self._bytes -= self._sizes.pop(key, 0)
            self._stats["evictions"] += 1

    def get_or_compute(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        """Return the cached value, computing it at most once per key."""
        value = self._lookup(key)
        if value is not _MISSING:
            return value

        stripe = self._stripes[hash(key) % len(self._stripes)]
        with stripe:
            value = self._lookup(key)
            if value is not _MISSING:
                return value
            # Bound process-wide CPU work; cache hits never take this path.
            with self._inflight:
                value = compute()
                if value is not None:
                    self.put(key, value)
                with self._lock:
                    self._stats["computes"] += 1
                return value


__all__ = ["ResultCache", "content_hash"]
