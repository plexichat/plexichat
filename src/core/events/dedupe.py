"""
Bounded dedupe registry for cross-worker pub/sub events.

Valkey pub/sub is at-most-once, but two real fan-out paths can still deliver
the same event to a worker more than once:

* the publishing worker receives its own echo.  The ``sw`` worker-id guard
  deliberately does NOT drop events tagged ``"unknown"`` (the pre-identity
  window at startup), so those echoes are otherwise re-dispatched to local
  clients on top of the local fan-out — a guaranteed duplicate delivery;
* a sibling worker reconnects or a publisher retries a burst, which can
  re-surface an event that was already ingested.

A single, bounded, TTL-expiring set of recently-seen event ids collapses
those duplicates.  The publisher records the id it just emitted and the
receiver checks/records on ingestion, sharing one registry so a worker
recognises its own echoes without relying solely on the ``sw`` tag.  The
registry is hard-bounded (``max_entries``) and time-bounded (``ttl_sec``),
so it cannot become an unbounded "retained message" memory leak.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Dict


class CrossWorkerDedupe:
    """Thread-safe, bounded, TTL-expiring dedupe set of event ids.

    Entries are keyed by a globally-unique event id (publisher worker id +
    uuid4).  Insertion order is preserved so a full registry evicts the
    oldest entries first (FIFO), giving a hard memory ceiling.  Expired
    entries are swept on every read, so an idle deployment does not retain
    ids forever.
    """

    def __init__(self, max_entries: int = 4096, ttl_sec: float = 300.0) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl_sec = float(ttl_sec)
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()
        # Observability counters — surface via health/dashboards.
        self._duplicates = 0
        self._evictions = 0

    def remember(self, event_id: str) -> None:
        """Record ``event_id`` as seen (called by the publisher).

        Recording at publish time is what lets a worker recognise its own
        echo: by the time the pub/sub message loops back, the id is already
        in the registry.  Empty ids are ignored (legacy payloads opt out of
        dedupe rather than being dropped).
        """
        if not event_id:
            return
        with self._lock:
            self._store(event_id)

    def is_duplicate(self, event_id: str) -> bool:
        """Return ``True`` if ``event_id`` was seen recently; record if new.

        Empty ids return ``False`` so pre-id payloads are still processed
        (backward compatibility — dedupe is opt-in via the envelope id).
        """
        if not event_id:
            return False
        with self._lock:
            self._sweep_expired_locked()
            if event_id in self._seen:
                self._duplicates += 1
                return True
            self._store(event_id)
            return False

    def _store(self, event_id: str) -> None:
        """Insert ``event_id`` and enforce the hard size bound.  Lock held."""
        self._seen[event_id] = time.monotonic()
        while len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
            self._evictions += 1

    def _sweep_expired_locked(self) -> None:
        """Drop ids older than the TTL.  Lock held."""
        cutoff = time.monotonic() - self._ttl_sec
        expired = [key for key, ts in self._seen.items() if ts < cutoff]
        for key in expired:
            self._seen.pop(key, None)
            self._evictions += 1

    def clear(self) -> None:
        """Reset the registry to its initial state.

        Both the remembered ids and the observability counters are dropped: a
        reset that kept the duplicate/eviction counts made anything reading
        ``metrics()`` afterwards depend on what the process had ingested
        before, which is what turned the regression tests order-sensitive.
        """
        with self._lock:
            self._seen.clear()
            self._duplicates = 0
            self._evictions = 0

    def metrics(self) -> Dict[str, int]:
        """Return a snapshot of the dedupe cache size and counters."""
        with self._lock:
            return {
                "size": len(self._seen),
                "max": self._max_entries,
                "duplicates": self._duplicates,
                "evictions": self._evictions,
            }


# Process-wide singleton shared by the publisher (EventManager) and the
# receiver (cross-worker websocket listener).  Kept in the events package so
# neither layer owns the other's import.
cross_worker_dedupe = CrossWorkerDedupe()
