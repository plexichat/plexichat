"""Bounded, short-window coalescer for repeated heartbeat presence writes.

Why this exists
---------------
``PresenceManager.update_last_seen`` is called on every WebSocket heartbeat
(and from any REST "ping" path). Clients may hold several connections for one
user, and a reconnect/retry can deliver many frames in the same second. The
30-second throttle in ``update_last_seen`` bounds the persisted ``last_seen``
UPDATE, but *every* call still pays for a throttle lookup, a full presence
rehydration and a fresh cache write (``_reseed_presence_cache``). A burst of
identical heartbeats therefore produced one cache write and several DB queries
per frame with no semantic change.

This tracker collapses those repeats into one real write per short window, per
user:

* **Bounded** - a fixed-size LRU. A flood of distinct user ids cannot grow it
  without limit; the least-recently-seen claim is evicted first.
* **Short-lived** - claims use a *monotonic* clock and are pruned exactly at the
  window boundary, so a pause in heartbeats always produces a fresh write.
* **Honest** - coalesced calls read the just-refreshed cache through
  ``get_presence(use_cache=True)``, so they report the actually persisted
  ``last_seen`` and never fabricate a newer timestamp.
* **Cache-authoritative** - coalesced calls read the just-refreshed presence
  cache, so an intervening status/focus change (which rewrites or deletes that
  entry) is reflected on the very next coalesced frame. `invalidate()` is
  available for callers that need to force a fresh write explicitly.

Failures to consult the tracker are not tolerated as stale data: a claim with
no recorded write (e.g. a concurrent first call still in flight) is treated as
a miss and the caller performs the full write path.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple

#: One second is far below both the 30s DB throttle and the ~45s gateway
#: heartbeat interval, so ordinary heartbeats are never suppressed; only
#: genuine same-user bursts are.
DEFAULT_WINDOW_SECONDS = 1.0

#: Ample for simultaneous heartbeats across many users while keeping retained
#: state tightly bounded.
DEFAULT_MAX_ENTRIES = 4096


class HeartbeatWriteClaim:
    """Outcome of :meth:`HeartbeatWriteCoalescer.claim`.

    ``write=True`` means the caller owns the write slot for this window and
    must run the full ``update_last_seen`` path, then call
    :meth:`HeartbeatWriteCoalescer.record`. ``write=False`` means the slot was
    already used; the caller may serve the warm cache snapshot instead.
    """

    __slots__ = ("write", "last_seen")

    def __init__(self, write: bool, last_seen: Optional[int] = None) -> None:
        self.write = write
        self.last_seen = last_seen


class HeartbeatWriteCoalescer:
    """Thread-safe LRU of per-user heartbeat-write claims."""

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
        # user_id -> (claimed_at_monotonic, last_seen_of_persisted_write)
        self._entries: "OrderedDict[int, Tuple[float, Optional[int]]]" = OrderedDict()
        self._coalesced = 0
        self._writes = 0
        self._lock = threading.Lock()

    def claim(self, user_id: int, coalesce: bool = True) -> HeartbeatWriteClaim:
        """Atomically decide whether ``user_id`` may write in this window."""
        uid = int(user_id)
        now = self._clock()
        with self._lock:
            self._prune_expired_locked(now)
            entry = self._entries.get(uid)
            # A claim whose write has not completed yet (no recorded last_seen)
            # cannot be reused: the second caller must write for itself rather
            # than serve a snapshot that does not exist yet.
            if (
                coalesce
                and entry is not None
                and entry[1] is not None
                and (now - entry[0]) < self._window
            ):
                self._coalesced += 1
                self._entries.move_to_end(uid)
                return HeartbeatWriteClaim(False, entry[1])

            self._writes += 1
            self._entries[uid] = (now, None)
            self._entries.move_to_end(uid)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return HeartbeatWriteClaim(True)

    def record(self, user_id: int, last_seen: int) -> None:
        """Store the persisted ``last_seen`` of a completed real write."""
        uid = int(user_id)
        with self._lock:
            entry = self._entries.get(uid)
            if entry is not None:
                self._entries[uid] = (entry[0], int(last_seen))

    def invalidate(self, user_id: Optional[int] = None) -> int:
        """Drop matching claims; return the number removed.

        A status change must force the next heartbeat to rehydrate and
        re-cache the row instead of reusing this window's snapshot.
        """
        with self._lock:
            if user_id is None:
                doomed = list(self._entries)
            else:
                doomed = [int(user_id)] if int(user_id) in self._entries else []
            for uid in doomed:
                self._entries.pop(uid, None)
            return len(doomed)

    def clear(self) -> None:
        """Drop every claim and reset counters (e.g. in tests)."""
        with self._lock:
            self._entries.clear()
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
        """Drop claims older than the window so expiry is exact, not lazy."""
        if not self._entries:
            return
        window = self._window
        for uid, value in list(self._entries.items()):
            if now - value[0] >= window:
                self._entries.pop(uid, None)
