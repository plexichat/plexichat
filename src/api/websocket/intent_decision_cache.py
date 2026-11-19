"""Bounded memo of gateway intent-filter decisions for event fan-out.

Why this exists
---------------
``filter_event_by_intents`` is called once per recipient connection for every
gateway event -- including events replayed from the cross-worker Valkey
channel, where each worker independently fans the event out to its own local
connections. The decision is a pure function of three values:

* the ``EventType``,
* the event's scope class (DM vs. guild), derived from whether ``server_id``
  is ``None`` and ``channel_id`` is set, and
* the connection's intent bitmask.

So a burst to N connections with the same intent mask recomputes the *same*
decision N times (and a cross-worker burst does it once per worker). Only a
handful of distinct intent masks exist in practice, so a tiny bounded memo
collapses that repeated work to one evaluation per distinct decision.

Correctness / security
----------------------
The cache key is the **complete** decision identity. Scope class is part of
the key: a guild event and a DM event of the same type with the same intent
mask are different decisions and can never share an entry. This matters
because the gateway maps have separate guild/DM intent tables -- serving a
DM decision for a guild event (or vice versa) would leak an event to the
wrong audience. Invalid intent masks are rejected before any cache lookup, so
the fail-closed check is never bypassed or weakened.

Entries carry a generation ("epoch") that is bumped by :meth:`clear`, giving
an explicit, O(1) way to drop every decision after an intent-rule change (or
between tests). The LRU is fixed-size, so retained decisions cannot grow
without bound.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, Optional, Tuple

#: Distinct (event type, scope, intent mask) triples are few. 256 is ample for
#: every event type carrying several common masks while keeping memory bounded.
DEFAULT_CACHE_ENTRIES = 256

_MISSING = object()

_Key = Tuple[int, object, bool, int]


class IntentDecisionCache:
    """Bounded, generation-keyed LRU of ``(type, is_dm, intents) -> bool``."""

    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[_Key, bool]" = OrderedDict()
        self._generation = 0
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def _key(self, event_type: object, is_dm: bool, intents: int) -> _Key:
        return (self._generation, event_type, is_dm, intents)

    def get(self, event_type: object, is_dm: bool, intents: int) -> Optional[bool]:
        """Return a cached decision, or ``None`` on a miss.

        ``False`` is a legitimate cached decision and is returned as-is; only
        ``None`` signals a miss.
        """
        key = self._key(event_type, is_dm, intents)
        with self._lock:
            cached = self._entries.get(key, _MISSING)
            if cached is not _MISSING:
                self._hits += 1
                self._entries.move_to_end(key)
                return cached  # type: ignore[return-value]
            self._misses += 1
            return None

    def put(self, event_type: object, is_dm: bool, intents: int, value: bool) -> None:
        """Store ``value`` under the current generation."""
        key = self._key(event_type, is_dm, intents)
        stored = bool(value)
        with self._lock:
            self._entries[key] = stored
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Invalidate every decision and reset counters (bumps the epoch)."""
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> Dict[str, int]:
        """Expose hit/miss/size counters and the epoch for observability."""
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "epoch": self._generation,
            }
