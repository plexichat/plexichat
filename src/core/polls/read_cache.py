"""Bounded, short-TTL cache for hydrated poll reads.

Poll detail and results reads hydrate a poll row, its options, a voter
aggregate and (for results) a per-option vote tally on every call. Those reads
are keyed only by ``(poll_id, viewer_id)`` and are safe to memoize for a very
short window *provided* two rules hold:

* the caller runs the cheap channel-access gate on every request **before**
  consulting this cache (so a revoked membership is never served a warm body);
* every poll mutation clears the whole cache (vote counts are visible to other
  viewers, so a per-poll entry cannot survive a write).

This is a process-local, best-effort accelerator: a miss is always recomputed
from the authoritative database, never assumed. All three limits are enforced
on every write so request-derived keys can never grow the cache without bound:

* ``max_entries`` caps live keys (LRU eviction on overflow);
* ``max_bytes`` caps the approximate footprint of cached values;
* ``ttl_seconds`` bounds staleness -- expired entries are never served.

The cache is thread-safe: all mutations and lookups run under one re-entrant
lock so the counters and byte total stay consistent under concurrent traffic.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Hashable, Optional


def _value_size(value: Any) -> int:
    """Cheap conservative byte estimate for a cached poll DTO.

    ``repr`` over-allocates for long text but that only makes the ceiling safer.
    Poll DTOs are small (a handful of options), so this is sufficient without
    walking every dataclass field.
    """
    if isinstance(value, str):
        return len(value.encode("utf-8", "ignore"))
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    return len(repr(value)) + 64


class PollReadCache:
    """A dict-like LRU with hard entry, byte and TTL bounds."""

    def __init__(
        self,
        max_entries: int = 512,
        max_bytes: int = 4 * 1024 * 1024,
        ttl_seconds: float = 10.0,
    ) -> None:
        if max_entries < 1:
            raise ValueError("PollReadCache max_entries must be at least 1")
        if max_bytes < 1:
            raise ValueError("PollReadCache max_bytes must be at least 1")
        if ttl_seconds <= 0:
            raise ValueError("PollReadCache ttl_seconds must be positive")
        self._max_entries = int(max_entries)
        self._max_bytes = int(max_bytes)
        self._ttl = float(ttl_seconds)
        self._data: "OrderedDict[Hashable, tuple[float, Any, int]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return a live value (refreshing recency) or ``default`` on miss.

        An expired entry is removed and treated as a miss, so a value can never
        be served past the TTL window.
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

    def set(self, key: Hashable, value: Any) -> None:
        """Store ``value``, evicting expired then least-recently-used entries."""
        now = time.monotonic()
        expires_at = now + self._ttl
        size = max(0, int(_value_size(value)))
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old[2]
            self._data[key] = (expires_at, value, size)
            self._bytes += size

            # Expired-but-never-read keys would otherwise linger and consume
            # the entry/byte budget until an LRU eviction happened to reach them.
            for expired_key, (exp, _v, sz) in list(self._data.items()):
                if exp <= now:
                    self._data.pop(expired_key, None)
                    self._bytes -= sz
                    self._expirations += 1

            while self._data and (
                len(self._data) > self._max_entries or self._bytes > self._max_bytes
            ):
                _evicted_key, (_exp, _v, sz) = self._data.popitem(last=False)
                self._bytes -= sz
                self._evictions += 1

            # A single value larger than max_bytes is not cached: dropping it
            # keeps the byte ceiling hard.
            if self._data and self._bytes > self._max_bytes:
                _k, (_exp, _v, sz) = self._data.popitem(last=False)
                self._bytes -= sz
                self._evictions += 1

    def clear(self) -> None:
        """Full invalidation: drop every entry (counters are preserved)."""
        with self._lock:
            self._data.clear()
            self._bytes = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def stats(self) -> dict:
        """Snapshot of counters and usage (used by tests and tooling)."""
        with self._lock:
            return {
                "size": len(self._data),
                "bytes": self._bytes,
                "max_entries": self._max_entries,
                "max_bytes": self._max_bytes,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "expirations": self._expirations,
            }
