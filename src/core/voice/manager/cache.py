"""Bounded, short-TTL cache for hot voice state and channel lookups.

Why this exists
---------------
The voice manager resolves a handful of immutable-on-read rows on every frame
of a voice session:

* ``get_voice_state(user_id)`` -- called on every ``VOICE_SPEAKING`` frame and
  twice inside every self-mute/self-deaf/stream/video update,
* ``_get_server_channel(channel_id)`` -- read on every join / move / channel
  detail lookup, and
* ``_get_channel_settings(channel_id)`` -- read on every join / move and
  channel detail lookup.

Each of those used to be a fresh ``SELECT``.  A single active speaker emits
tens of speaking frames per second, so a read-only frame incurred the same
database round trip as a state-changing one.  This module memoizes those
lookups for a short epoch so a burst collapses into one query.

Safety properties (pinned by ``src/tests/voice/test_voice_lookup_cache.py``):

* **Bounded** -- a fixed-size LRU; a flood of distinct user/channel ids cannot
  grow the cache without limit.
* **Short-lived** -- entries carry a monotonic expiry so a write made by
  another process propagates within one TTL window.  Expiry is enforced on
  read (no stale value is ever returned).
* **Explicit invalidation** -- every voice-manager write path drops the
  affected user/channel entries before its read-after-write, so an update is
  never served its own stale value.
* **Negative caching** -- "user is not in voice" is cached too, so a speaking
  frame from a non-member does not query the database on every frame.
* **Missing-safe** -- callers distinguish a cached ``None`` from a miss with
  the exported :data:`_MISS` sentinel and recompute from the database on a
  miss; eviction is always safe.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Hashable, Tuple

#: Hard cap on live entries. A few thousand users/channels is ample for even a
#: large server while keeping retained memory tightly bounded.
DEFAULT_MAX_ENTRIES = 2048

#: Short TTL bounds cross-process staleness to one window while collapsing a
#: multi-frame speaking / join burst into a single query.
DEFAULT_TTL_SECONDS = 2.0

Key = Tuple[str, Any]

#: Sentinel returned by :meth:`VoiceLookupCache.get` on a miss. Distinct from
#: ``None`` because ``None`` is a legitimate cached value ("not in voice").
_MISS = object()


class VoiceLookupCache:
    """A thread-safe bounded LRU with a short monotonic TTL.

    ``clock`` is injectable so tests can advance time without sleeping.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = float(ttl_seconds)
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._data: "OrderedDict[Key, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0

    # --- key helpers -----------------------------------------------------
    @staticmethod
    def state_key(user_id: Hashable) -> Key:
        return ("state", int(user_id))

    @staticmethod
    def channel_key(channel_id: Hashable) -> Key:
        return ("channel", int(channel_id))

    @staticmethod
    def settings_key(channel_id: Hashable) -> Key:
        return ("settings", int(channel_id))

    # --- core operations -------------------------------------------------
    def get(self, key: Key) -> Any:
        """Return a live value (refreshing recency) or ``_MISS`` on a miss."""
        now = self._clock()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self._misses += 1
                return _MISS
            expires_at, value = entry
            if expires_at <= now:
                del self._data[key]
                self._expirations += 1
                self._misses += 1
                return _MISS
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: Key, value: Any) -> None:
        """Store ``value``, evicting the least-recently-used entry at capacity."""
        now = self._clock()
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            elif len(self._data) >= self._max_entries:
                self._data.popitem(last=False)
                self._evictions += 1
            self._data[key] = (now + self._ttl, value)

    # --- invalidation ----------------------------------------------------
    def invalidate(self, *keys: Key) -> int:
        """Drop the given keys; return the number actually removed."""
        removed = 0
        with self._lock:
            for key in keys:
                if self._data.pop(key, None) is not None:
                    removed += 1
        return removed

    def invalidate_state(self, user_id: Hashable) -> int:
        return self.invalidate(self.state_key(user_id))

    def invalidate_channel(self, channel_id: Hashable) -> int:
        cid = int(channel_id)
        return self.invalidate(self.channel_key(cid), self.settings_key(cid))

    def clear(self) -> None:
        """Full invalidation: drop every entry (counters are preserved)."""
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    # --- observability ---------------------------------------------------
    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "expirations": self._expirations,
            }


__all__ = [
    "VoiceLookupCache",
    "Key",
    "_MISS",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
]
