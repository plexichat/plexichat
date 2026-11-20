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
from typing import Dict, Union

#: Hard ceiling for the cumulative observability counters.  A long-lived
#: worker can ingest billions of events; the registry's ``_seen`` set is capped
#: by ``max_entries``/TTL, but the ``duplicates``/``evictions`` metrics kept
#: growing for the entire process lifetime (only the test-only ``clear`` reset
#: them).  Saturating at a fixed ceiling keeps those counters O(1) in memory
#: while a companion ``*_saturated`` flag preserves observability: once set, a
#: counter means "at least MAX_COUNTER" instead of an exact total.  The
#: dedupe decisions themselves are untouched by this bound.
MAX_COUNTER = (1 << 32) - 1


class CrossWorkerDedupe:
    """Thread-safe, bounded, TTL-expiring dedupe set of event ids.

    Entries are keyed by a globally-unique event id (publisher worker id +
    uuid4).  Insertion order is preserved so a full registry evicts the
    oldest entries first (FIFO), giving a hard memory ceiling.  Expired
    entries are swept on every read, so an idle deployment does not retain
    ids forever.
    """

    def __init__(
        self,
        max_entries: int = 4096,
        ttl_sec: float = 300.0,
        max_counter: int = MAX_COUNTER,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl_sec = float(ttl_sec)
        self._max_counter = max(1, int(max_counter))
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()
        # Observability counters — surface via health/dashboards.  These are
        # saturating (see ``MAX_COUNTER``) so they cannot grow without reset
        # over a long-lived worker's lifetime.
        self._duplicates = 0
        self._evictions = 0
        self._duplicates_saturated = False
        self._evictions_saturated = False

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
                self._record_duplicate_locked()
                return True
            self._store(event_id)
            return False

    def _record_duplicate_locked(self) -> None:
        """Bump the duplicate counter with a hard ceiling.  Lock held."""
        if self._duplicates >= self._max_counter:
            self._duplicates_saturated = True
        else:
            self._duplicates += 1

    def _record_eviction_locked(self) -> None:
        """Bump the eviction counter with a hard ceiling.  Lock held."""
        if self._evictions >= self._max_counter:
            self._evictions_saturated = True
        else:
            self._evictions += 1

    def _store(self, event_id: str) -> None:
        """Insert ``event_id`` and enforce the hard size bound.  Lock held.

        Re-storing an id (a publisher retrying a burst with the same envelope
        id) deletes then reinserts it so it moves to the end with a fresh
        timestamp.  That keeps ``_seen`` ordered oldest-first by insertion
        time, which is the invariant ``_sweep_expired_locked`` relies on to
        stop at the first live entry instead of scanning the whole registry.
        """
        if event_id in self._seen:
            del self._seen[event_id]
        self._seen[event_id] = time.monotonic()
        while len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
            self._record_eviction_locked()

    def _sweep_expired_locked(self) -> None:
        """Drop ids older than the TTL.  Lock held.

        ``_store`` keeps the registry ordered by time (oldest first), so every
        expired id forms a prefix.  Walk only that prefix and stop at the first
        live entry: the common case (nothing expired yet) is one O(1) check
        rather than an O(max_entries) scan plus a throwaway list allocation on
        every single event ingestion on the cross-worker hot path.
        """
        cutoff = time.monotonic() - self._ttl_sec
        while self._seen:
            event_id = next(iter(self._seen))
            if self._seen[event_id] >= cutoff:
                break
            self._seen.popitem(last=False)
            self._record_eviction_locked()

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
            self._duplicates_saturated = False
            self._evictions_saturated = False

    def metrics(self) -> Dict[str, Union[int, bool]]:
        """Return a snapshot of the dedupe cache size and counters.

        ``duplicates``/``evictions`` saturate at ``max_counter``; the matching
        ``*_saturated`` flags tell operators when a value is a lower bound
        rather than an exact total.  ``size``/``max`` remain exact so the hard
        retention bound is still directly observable.
        """
        with self._lock:
            return {
                "size": len(self._seen),
                "max": self._max_entries,
                "duplicates": self._duplicates,
                "evictions": self._evictions,
                "duplicates_saturated": self._duplicates_saturated,
                "evictions_saturated": self._evictions_saturated,
                "max_counter": self._max_counter,
            }


# Process-wide singleton shared by the publisher (EventManager) and the
# receiver (cross-worker websocket listener).  Kept in the events package so
# neither layer owns the other's import.
cross_worker_dedupe = CrossWorkerDedupe()
