"""Bounded, in-flight-deduplicating memo for expensive metadata probes.

Video metadata extraction spawns ``ffprobe`` as a subprocess. The same bytes
are frequently resolved more than once (list refreshes, retries, concurrent
requests for the same attachment), and each call used to pay for a fresh
process. This module memoizes the outcome for a short TTL and coalesces
simultaneous identical requests so only one probe is in flight per key.

Security properties (the reason the key is deliberately wide):

* Every key contains the **full scope** of the probe -- the caller-supplied
  scope string plus the SHA-256 of the complete input bytes. Results computed
  under one scope (a different ffprobe binary, timeout or size limit) can never
  be served to another, and one file's metadata can never be served for a
  different file, so there is no cross-user / cross-tenant leak.
* Only successful results are retained. Failures are shared with concurrent
  waiters for the duration of the in-flight call but are never memoized, so a
  transient ``ffprobe`` error can be retried on the next request.
* Both entry count and TTL are bounded, and a hard cap on in-flight keys stops
  a hostile stream of distinct inputs from pinning unbounded waiter state.
  Callers can drop everything (or one scope) with :meth:`clear` on mutation.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Hashable, Optional, Tuple


class _InFlight:
    """One shared outcome for a set of concurrent identical callers."""

    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.error: Optional[BaseException] = None


class MetadataResultCache:
    """Thread-safe bounded TTL LRU with per-key in-flight coalescing."""

    def __init__(
        self,
        maxsize: int = 128,
        ttl_seconds: float = 30.0,
        max_inflight: int = 64,
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        self._ttl = max(0.0, float(ttl_seconds))
        self._max_inflight = max(1, int(max_inflight))
        # key -> (expires_at_monotonic, value)
        self._entries: "OrderedDict[Hashable, Tuple[float, Any]]" = OrderedDict()
        self._inflight: Dict[Hashable, _InFlight] = {}
        self._lock = threading.Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "computes": 0,
            "coalesced": 0,
            "expired": 0,
            "evictions": 0,
            "inflight_rejected": 0,
            "inflight_peak": 0,
        }

    @staticmethod
    def make_key(scope: str, content_hash: str) -> Tuple[str, str]:
        """Build the full, scope-partitioned cache key.

        ``scope`` carries the probe's security context; ``content_hash`` is a
        digest of the *entire* input. Both are required: omitting either would
        let one credential/file reuse another's cached metadata.
        """
        return (str(scope), str(content_hash))

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return a live cached value or ``default`` (no computation)."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return default
            expires, value = entry
            if expires <= now:
                self._entries.pop(key, None)
                self._stats["expired"] += 1
                return default
            self._entries.move_to_end(key)
            self._stats["hits"] += 1
            return value

    def get_or_compute(
        self,
        key: Hashable,
        compute: Callable[[], Any],
    ) -> Any:
        """Return the cached value, computing it at most once per live key.

        Concurrent callers for the same key wait on a shared ``Event`` and
        receive the leader's result (or the same exception). Only successful
        results are inserted into the bounded LRU.
        """
        now = time.monotonic()
        leader = False
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                expires, value = entry
                if expires > now:
                    self._entries.move_to_end(key)
                    self._stats["hits"] += 1
                    return value
                self._entries.pop(key, None)
                self._stats["expired"] += 1

            flight = self._inflight.get(key)
            if flight is None:
                if len(self._inflight) >= self._max_inflight:
                    # Rejecting (rather than blocking forever) bounds waiter
                    # state; the caller falls back to an independent compute.
                    self._stats["inflight_rejected"] += 1
                    leader = True
                    flight = _InFlight()
                else:
                    flight = _InFlight()
                    self._inflight[key] = flight
                    leader = True
                    self._stats["misses"] += 1
                    self._stats["inflight_peak"] = max(
                        self._stats["inflight_peak"], len(self._inflight)
                    )
            else:
                self._stats["coalesced"] += 1

        if not leader:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value

        # Leader computes outside the lock so unrelated keys never serialize.
        try:
            value = compute()
        except BaseException as exc:
            with self._lock:
                flight.error = exc
                if self._inflight.get(key) is flight:
                    self._inflight.pop(key, None)
            flight.event.set()
            raise

        with self._lock:
            flight.value = value
            if self._inflight.get(key) is flight:
                self._inflight.pop(key, None)
            if self._ttl > 0:
                self._entries[key] = (time.monotonic() + self._ttl, value)
                self._entries.move_to_end(key)
                self._stats["computes"] += 1
                while len(self._entries) > self._maxsize:
                    self._entries.popitem(last=False)
                    self._stats["evictions"] += 1
        flight.event.set()
        return value

    def clear(self, scope: Optional[str] = None) -> None:
        """Drop all entries, or only those in ``scope``, on mutation/reload."""
        with self._lock:
            if scope is None:
                self._entries.clear()
                return
            prefix = str(scope)
            for key in [k for k in self._entries if k[0] == prefix]:
                self._entries.pop(key, None)

    def stats(self) -> dict:
        """Snapshot of counters (for tests/observability)."""
        with self._lock:
            result = dict(self._stats)
            result["size"] = len(self._entries)
            result["inflight"] = len(self._inflight)
            return result

    def reset_stats(self) -> None:
        with self._lock:
            for key in self._stats:
                self._stats[key] = 0


__all__ = ["MetadataResultCache"]
