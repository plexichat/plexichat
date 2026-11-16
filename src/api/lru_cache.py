"""
Thread-safe bounded LRU cache used by the API app factory.

Extracted from app.py to keep that module focused on application
wiring; import site in app.py is unchanged in behavior.

The cache is instrumented with hit/miss/eviction counters so its effectiveness
can be observed at runtime and asserted in regression tests. All counters are
updated under the same lock as the underlying dict, so they stay consistent
under concurrent access.
"""

from collections import OrderedDict
import threading


class LRUCache:
    """Thread-safe bounded LRU cache that behaves like a dict for get/setitem/contains."""

    def __init__(self, maxsize: int = 1024):
        if maxsize < 1:
            raise ValueError("LRUCache maxsize must be at least 1")
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def maxsize(self) -> int:
        """The upper bound on the number of cached entries."""
        return self._maxsize

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def get(self, key, default=None):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return default

    def __setitem__(self, key, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            # Bounded by construction: every insertion over the cap evicts the
            # least-recently-used entry, and the eviction is counted so callers
            # can see when the bound is actively churning.
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
                self._evictions += 1

    def __contains__(self, key):
        with self._lock:
            return key in self._cache

    def pop(self, key, default=None):
        with self._lock:
            return self._cache.pop(key, default)

    def clear(self) -> None:
        """Remove all entries (counters are untouched)."""
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict:
        """Return a snapshot of hit/miss/eviction counters and current size."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "size": len(self._cache),
                "maxsize": self._maxsize,
            }

    def reset_stats(self) -> None:
        """Zero the counters without disturbing cached entries."""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0
