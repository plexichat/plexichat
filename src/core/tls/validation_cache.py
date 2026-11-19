"""Bounded TTL cache for the results of expensive TLS validation.

Certificate chain verification parses PEM, checks every signature and validates
validity windows. Repeating it for the same certificate/hostname/pins on every
startup or worker boot is pure cost, but an unbounded or forever-valid cache is
a security bug: rotated certificates or a different hostname must never reuse a
previous verdict.

This cache is deliberately small and closed:

* a hard entry cap with least-recently-used eviction (no unbounded growth);
* a monotonic TTL for every entry (no immortal validation results);
* an optional wall-clock ``valid_until`` bound derived from the certificate
  validity window, so an entry can never outlive the certificate it describes;
* exact-key matching: callers include the certificate bytes digest, hostname
  and pins in the key, which makes rotation and wrong-domain resumption misses;
* a lock around every mutation, so multi-worker/multi-thread startup is safe.

Failures to look up are misses (recompute), never a reason to trust stale data.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Generic, Hashable, Optional, TypeVar

DEFAULT_MAX_ENTRIES = 64
DEFAULT_TTL_SECONDS = 60.0

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass(frozen=True)
class CacheStats:
    """Counters for observing cache behaviour without reaching into internals."""

    size: int
    hits: int
    misses: int
    evictions: int
    expirations: int


@dataclass(frozen=True)
class _Entry(Generic[V]):
    value: V
    expires_at: float
    valid_until: Optional[datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BoundedTTLCache(Generic[K, V]):
    """A thread-safe LRU cache whose entries expire after ``ttl_seconds``.

    ``clock``/``now`` are injectable for deterministic tests; production uses
    ``time.monotonic`` for the TTL (immune to wall-clock jumps) and UTC wall
    time for the optional certificate-validity bound.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        clock: Optional[Callable[[], float]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise ValueError("max_entries must be a positive integer")
        if max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ValueError("ttl_seconds must be a positive finite number")
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl_seconds must be a positive finite number")
        self.max_entries = max_entries
        self.ttl_seconds = ttl
        self._clock = clock or time.monotonic
        self._now = now or _utcnow
        self._entries: "OrderedDict[K, _Entry[V]]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0
        self._lock = threading.Lock()

    def _is_expired(self, entry: _Entry[V]) -> bool:
        if self._clock() >= entry.expires_at:
            return True
        if entry.valid_until is not None:
            current = self._now()
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if current >= entry.valid_until:
                return True
        return False

    def _purge_expired(self) -> None:
        expired = [
            key for key, entry in self._entries.items() if self._is_expired(entry)
        ]
        for key in expired:
            del self._entries[key]
            self._expirations += 1

    def get(self, key: K) -> Optional[V]:
        """Return the live value for ``key`` or ``None`` (a miss)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if self._is_expired(entry):
                del self._entries[key]
                self._expirations += 1
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry.value

    def put(self, key: K, value: V, *, valid_until: Optional[datetime] = None) -> None:
        """Store ``value``, evicting expired and then least-recently-used entries."""
        with self._lock:
            self._purge_expired()
            if key in self._entries:
                del self._entries[key]
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1
            self._entries[key] = _Entry(
                value=value,
                expires_at=self._clock() + self.ttl_seconds,
                valid_until=valid_until,
            )

    def clear(self) -> None:
        """Drop every entry (e.g. after certificate material is regenerated)."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._entries)

    @property
    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                size=len(self._entries),
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                expirations=self._expirations,
            )


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "BoundedTTLCache",
    "CacheStats",
]
