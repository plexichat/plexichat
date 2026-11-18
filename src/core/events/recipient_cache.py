"""Bounded, short-lived cache of resolved gateway-event recipient lists.

Why this exists
---------------
High-frequency channel-scoped gateway events (typing indicators, reaction
add/remove, read receipts) resolve their audience on every emit through
``resolve_channel_event_recipients``. That resolution walks the channel, lists
every server member and runs a ``channels.view`` permission check per member —
even when a burst of events targets the exact same channel/guild/actor with the
exact same exclusions. On a busy channel this repeats the whole walk (and
thousands of permission lookups) for every single event.

This module memoizes the *fully resolved* recipient list for a short epoch so a
burst pays for one resolution. It is deliberately conservative:

* **Bounded** — a fixed-size LRU, so retained payloads/recipient lists cannot
  grow without limit.
* **Short-lived** — keys carry a time-bucket epoch (``ttl_seconds``), so a
  membership or permission change propagates within one TTL window.
* **Keyed by the complete resolution identity** — channel, guild, actor,
  exclusions *and* the identity of the audience-source modules. Any input that
  can change the audience produces a different key, so one audience's list can
  never be served for another's. The caller still applies intent filtering at
  dispatch time; this cache only affects *who is resolved*, never whether a
  resolved recipient's connection receives the event.

Fail-closed results (empty lists) are cached too: the safe direction is to
withhold an event for up to one TTL window, never to widen an audience.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

#: 128 small recipient lists is ample for a burst across many channels while
#: keeping the retained memory tightly bounded.
DEFAULT_CACHE_ENTRIES = 128

#: One second bounds audience staleness tightly enough for a permission or
#: membership change to become visible almost immediately, while still
#: collapsing a rapid typing/reaction burst into a single resolution.
DEFAULT_TTL_SECONDS = 1.0

_MISSING = object()


class RecipientResolutionCache:
    """LRU of resolved recipient lists keyed by scope identity + time epoch."""

    def __init__(
        self,
        max_entries: int = DEFAULT_CACHE_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl_seconds = float(ttl_seconds)
        if self._ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._clock = clock or time.monotonic
        self._entries: "OrderedDict[Tuple[Any, ...], List[int]]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def epoch(self) -> int:
        """Current time bucket. All keys in a bucket share one resolution."""
        return int(self._clock() / self._ttl_seconds)

    def _key(self, parts: Tuple[Any, ...]) -> Tuple[Any, ...]:
        return (self.epoch(), tuple(parts))

    def get(self, parts: Tuple[Any, ...]) -> Optional[List[int]]:
        """Return a copy of the cached recipients for ``parts``, or ``None``.

        A copy is returned so a caller mutating its recipient list cannot
        corrupt a later reuse of the same resolution.
        """
        key = self._key(parts)
        with self._lock:
            cached = self._entries.get(key, _MISSING)
            if cached is not _MISSING:
                self._hits += 1
                self._entries.move_to_end(key)
                return list(cached)  # type: ignore[arg-type]
            self._misses += 1
            return None

    def put(self, parts: Tuple[Any, ...], recipients: List[int]) -> None:
        """Store a resolution under ``parts`` for the current epoch."""
        key = self._key(parts)
        stored = list(recipients)
        with self._lock:
            self._prune_stale_locked()
            self._entries[key] = stored
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def _prune_stale_locked(self) -> None:
        """Drop entries from earlier epochs so TTL expiry is exact, not lazy."""
        current = self.epoch()
        for key in [k for k in self._entries if k[0] != current]:
            self._entries.pop(key, None)

    def stats(self) -> Dict[str, int]:
        """Hit/miss counters and size, for observability and tests."""
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
            }

    def clear(self) -> None:
        """Drop all cached resolutions (e.g. on module re-setup or in tests)."""
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
