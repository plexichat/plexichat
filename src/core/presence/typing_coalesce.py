"""Bounded, short-window coalescer for repeated typing writes.

Why this exists
---------------
``PresenceManager.start_typing`` is called on every TYPING_START frame. Clients
re-send that frame while a user keeps typing, and the REST and WebSocket paths
can both fire for the same keystroke burst. Every call used to perform two
unconditional database writes: a global ``DELETE FROM pres_typing WHERE
expires_at < now`` and an ``UPSERT`` of the same (user, channel) row. A burst of
identical frames therefore hammered the database for no semantic gain — only
the last frame in the burst matters.

This tracker coalesces those redundant writes into one per short window, per
``(user_id, channel_id)`` pair:

* **Bounded** — a fixed-size LRU. A flood of distinct channels cannot grow it
  without limit; the oldest claim is evicted first.
* **Short-lived** — claims use a *monotonic* clock and expire exactly after the
  window on every claim, so no stale claim survives a pause in typing.
* **Valkey-gated** — callers only coalesce when the live typing state is known
  to be in Valkey. Without it the DB row is the sole source of truth and every
  frame must still refresh it (see ``TypingMixin.start_typing``).
* **Explicitly invalidated** — ``stop_typing`` / ``clear_all_typing`` drop the
  affected claims so a stop→start in the same window still writes the row.

Why expiry is a heap, not a full LRU scan
----------------------------------------
``claim()`` runs on every TYPING_START frame. Walking the whole bounded LRU
(up to 4096 pairs) to find expired claims on every frame made one keystroke's
cost proportional to the number of tracked pairs, not to the number of claims
that actually expired. Expiries now live in a min-heap keyed by deadline, so
pruning touches only entries whose window really ended (O(log N) each) and an
unexpired claim is O(1). Stale heap items (from re-claim, ``invalidate`` or
LRU eviction) are skipped by generation and the heap is compacted when it
outgrows a small multiple of the bounded LRU. Coalescing decisions, persisted
timestamps and counters are unchanged.

The stored ``(started_at, expires_at)`` of the last real write is handed back to
coalesced callers so the returned :class:`TypingIndicator` reports the *actual*
persisted timestamps instead of fabricating an expiry that was never written.
"""

from __future__ import annotations

import heapq
import threading
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

#: One second is far below the 6s typing timeout, so normal client frames
#: (a few seconds apart) are never suppressed; only genuine bursts are.
DEFAULT_WINDOW_SECONDS = 1.0

#: Ample for simultaneous typing bursts across many channels while keeping the
#: retained state tightly bounded.
DEFAULT_MAX_ENTRIES = 4096

#: Rebuild the expiry heap once it grows past this multiple of the LRU size.
#: Replacing stale heap items is amortised O(1) per claim and keeps the heap
#: bounded even under sustained invalidate/re-claim churn.
_EXPIRY_HEAP_FACTOR = 2

Key = Tuple[int, int]


class TypingWriteClaim:
    """Outcome of :meth:`TypingWriteCoalescer.claim`.

    ``write=True`` means the caller owns the write slot for this window and
    must perform the DB write then call :meth:`TypingWriteCoalescer.record`.
    ``write=False`` means the write was coalesced; the persisted timestamps of
    the last real write are provided so the caller can return an honest value.
    """

    __slots__ = ("write", "started_at", "expires_at")

    def __init__(
        self,
        write: bool,
        started_at: Optional[int] = None,
        expires_at: Optional[int] = None,
    ) -> None:
        self.write = write
        self.started_at = started_at
        self.expires_at = expires_at


class TypingWriteCoalescer:
    """Thread-safe LRU of per-(user, channel) typing-write claims with heap pruning."""

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock=None,
    ) -> None:
        self._window = float(window_seconds)
        if self._window <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_entries = max(1, int(max_entries))
        self._clock = clock or time.monotonic
        # key -> (claimed_at_monotonic, started_at, expires_at, generation)
        # ``generation`` discards stale heap items after a re-claim/invalidate.
        self._entries: "OrderedDict[Key, Tuple[float, int, int, int]]" = OrderedDict()
        # Min-heap of (deadline_monotonic, generation, key). Only the head is
        # inspected on claim, so an unexpired tracker pays O(1), not O(N).
        self._expiries: "list[Tuple[float, int, Key]]" = []
        self._generation = 0
        self._coalesced = 0
        self._writes = 0
        self._lock = threading.Lock()

    def claim(self, key: Key, coalesce: bool = True) -> TypingWriteClaim:
        """Atomically decide whether ``key`` may write in this window.

        When ``coalesce`` is False the caller has no authoritative live state
        (e.g. Valkey is down), so the write is always allowed.
        """
        key = (int(key[0]), int(key[1]))
        now = self._clock()
        with self._lock:
            self._prune_expired_locked(now)
            entry = self._entries.get(key)
            if coalesce and entry is not None and (now - entry[0]) < self._window:
                self._coalesced += 1
                self._entries.move_to_end(key)
                return TypingWriteClaim(False, entry[1], entry[2])

            self._writes += 1
            self._generation += 1
            generation = self._generation
            stored_started = entry[1] if entry is not None else 0
            stored_expires = entry[2] if entry is not None else 0
            self._entries[key] = (now, stored_started, stored_expires, generation)
            heapq.heappush(self._expiries, (now + self._window, generation, key))
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            self._compact_expiries_locked()
            return TypingWriteClaim(True)

    def record(self, key: Key, started_at: int, expires_at: int) -> None:
        """Store the timestamps of a completed real write for ``key``."""
        key = (int(key[0]), int(key[1]))
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                # Keep the original claim time and generation: the deadline
                # depends on when the slot was claimed, not when it was filled.
                self._entries[key] = (
                    entry[0],
                    int(started_at),
                    int(expires_at),
                    entry[3],
                )

    def invalidate(
        self, key: Optional[Key] = None, user_id: Optional[int] = None
    ) -> int:
        """Drop matching claims; return the number removed.

        ``key`` narrows to a single (user, channel) pair, ``user_id`` to every
        channel for one user. With neither argument the whole tracker is
        cleared. Called on typing stop/clear so a stop→start burst cannot leave
        a live Valkey member without its backing DB row.
        """
        with self._lock:
            if key is None and user_id is None:
                doomed = list(self._entries)
            else:
                key = (int(key[0]), int(key[1])) if key is not None else None
                uid = int(user_id) if user_id is not None else None
                doomed = [
                    k
                    for k in self._entries
                    if (key is not None and k == key)
                    or (uid is not None and k[0] == uid)
                ]
            for k in doomed:
                self._entries.pop(k, None)
            return len(doomed)

    def clear(self) -> None:
        """Drop every claim and reset counters (e.g. in tests)."""
        with self._lock:
            self._entries.clear()
            self._expiries.clear()
            self._generation = 0
            self._coalesced = 0
            self._writes = 0

    def stats(self) -> Dict[str, int]:
        """Claim/coalesce counters and current size, for tests/observability."""
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "coalesced": self._coalesced,
                "writes": self._writes,
            }

    def _prune_expired_locked(self, now: float) -> None:
        """Drop only claims whose deadline has passed.

        A min-heap keeps deadline-ordered work proportional to the number of
        *expired* claims. Heap items whose generation no longer matches the
        live entry (re-claimed, invalidated or LRU-evicted) are skipped lazily.
        """
        expiries = self._expiries
        entries = self._entries
        while expiries and expiries[0][0] <= now:
            _deadline, generation, key = heapq.heappop(expiries)
            entry = entries.get(key)
            if entry is None or entry[3] != generation:
                continue
            del entries[key]

    def _compact_expiries_locked(self) -> None:
        """Rebuild the heap from live entries when stale items accumulate."""
        if len(self._expiries) <= _EXPIRY_HEAP_FACTOR * self._max_entries:
            return
        self._expiries = [
            (claimed_at + self._window, generation, key)
            for key, (
                claimed_at,
                _started,
                _expires,
                generation,
            ) in self._entries.items()
        ]
        heapq.heapify(self._expiries)
