"""Bounded, short-TTL cache of per-channel typing audiences.

Why this exists
---------------
The recipients of a typing event in a server channel are a property of the
*channel* (its members who can view it), not of the sender. Resolving that set
is expensive: it lists every server member and runs a ``channels.view``
permission check for each one. A typing burst in a large channel used to repeat
that whole walk for every frame.

This cache memoizes the sender-agnostic viewer set per channel for a short
epoch. The caller removes the current sender from the cached set when building
an audience, so a single resolution is safely shared by every sender in a
typing burst. Storing the *full* viewer set (instead of the set minus one
particular sender) is what makes the entry safe to share: no sender's identity
is baked into the cached value, so one user's audience can never leak into
another's.

The design mirrors :mod:`src.core.events.recipient_cache`:

* **Bounded** — a fixed-size LRU, so retained audience lists cannot grow
  without limit even under a flood of distinct channels.
* **Short-lived and RBAC-versioned** — keys carry both a monotonic time epoch
  and the server's RBAC epoch, so a membership or permission change propagates
  within one TTL window and exact expiry is enforced on write instead of
  relying on lazy reads. Folding the RBAC epoch into the key means a role edit
  that revokes a member's ``channels.view`` retires that member's stale viewer
  set on the very next read, not after the whole TTL.
* **Copy-safe** — :meth:`get` returns a fresh list so a caller mutating its
  audience cannot corrupt a later reuse of the same resolution.
* **Explicit invalidation** — :meth:`invalidate` drops matching channels on a
  presence change so a stale audience never survives a write.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

#: 4096 small viewer sets is ample for bursts across many channels while
#: keeping the retained memory tightly bounded. (Matches the previous bound.)
DEFAULT_CACHE_ENTRIES = 4096

#: Ten seconds bounds audience staleness tightly enough for a revoked member to
#: receive at most one stale burst, while collapsing a typing burst into a
#: single resolution. (Matches the previous typing-audience TTL.)
DEFAULT_TTL_SECONDS = 10.0

_MISSING = object()


class TypingAudienceCache:
    """LRU of per-channel viewer sets keyed by scope identity + time epoch."""

    def __init__(
        self,
        max_entries: int = DEFAULT_CACHE_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock=None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl_seconds = float(ttl_seconds)
        if self._ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._clock = clock or time.monotonic
        # (time_epoch, rbac_epoch, server_id, channel_id) -> viewer set. The
        # RBAC epoch is bumped by every role/membership/override/ownership
        # write, so a revoked channels.view verdict becomes unreachable now.
        self._entries: "OrderedDict[Tuple[int, int, int, int], Tuple[int, ...]]" = (
            OrderedDict()
        )
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def _epoch(self) -> int:
        return int(self._clock() / self._ttl_seconds)

    @staticmethod
    def _rbac_epoch(server_id: int) -> Optional[int]:
        """Current role epoch for ``server_id``, or ``None`` on any failure."""
        try:
            from src.core.servers.channel_access_cache import get_channel_access_cache

            return int(get_channel_access_cache().version(int(server_id), 0)[0])
        except Exception:
            return None

    def get(self, server_id: int, channel_id: int) -> Optional[List[int]]:
        """Return a copy of the cached viewers for a channel, or ``None``.

        The returned set is the full list of users who may view the channel;
        the caller must remove the current sender before dispatching.
        """
        rbac = self._rbac_epoch(server_id)
        if rbac is None:
            with self._lock:
                self._misses += 1
            return None
        key = (self._epoch(), rbac, int(server_id), int(channel_id))
        with self._lock:
            cached = self._entries.get(key, _MISSING)
            if cached is not _MISSING:
                self._hits += 1
                self._entries.move_to_end(key)
                # Defence in depth: a value written before the type guard in
                # ``put`` was added must never launder a non-int viewer into a
                # genuine principal on a warm read.
                return [uid for uid in cached if type(uid) is int]  # type: ignore[union-attr]
            self._misses += 1
            return None

    def put(self, server_id: int, channel_id: int, viewers: Iterable[int]) -> None:
        """Store a channel's full viewer set under the current epochs.

        Only genuine integer ids are admitted. ``get_member_user_ids`` is the
        raw ``srv_members.user_id`` projection with no type filtering, and the
        permission resolver coerces ids with ``int()``; a malformed-but-coercible
        value (``"05"``, ``" 8 "``, ``True``, ``5.9``) can pass ``channels.view``
        and reach this boundary. Coercing it here with ``int(uid)`` would launder
        it into a principal that never proved view rights, and because a warm
        ``get`` returns plain ints it would then sail past the fan-out boundary's
        ``type(uid) is int`` filter — admitting the wrong viewer to
        TYPING_START/STOP. Reject non-int values instead (``type()`` also rejects
        bool). Genuine integer members are preserved verbatim.
        """
        rbac = self._rbac_epoch(server_id)
        if rbac is None:
            return
        key = (self._epoch(), rbac, int(server_id), int(channel_id))
        stored = tuple(uid for uid in viewers if type(uid) is int)
        with self._lock:
            self._prune_stale_locked()
            self._entries[key] = stored
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def invalidate(
        self, server_id: Optional[int] = None, channel_id: Optional[int] = None
    ) -> int:
        """Drop matching entries across all epochs; return the count removed.

        With no arguments the whole (process-local) cache is dropped. Supplying
        a server and/or channel id narrows the invalidation to matching scopes.
        """
        with self._lock:
            doomed = []
            for key in self._entries:
                _, _, sid, cid = key
                if server_id is not None and sid != int(server_id):
                    continue
                if channel_id is not None and cid != int(channel_id):
                    continue
                doomed.append(key)
            for key in doomed:
                self._entries.pop(key, None)
            return len(doomed)

    def clear(self) -> None:
        """Drop every cached resolution and reset counters (e.g. in tests)."""
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> Dict[str, int]:
        """Hit/miss counters and current size, for observability and tests."""
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
            }

    def _prune_stale_locked(self) -> None:
        """Drop entries from earlier epochs so TTL expiry is exact, not lazy."""
        current = self._epoch()
        for key in [k for k in self._entries if k[0] != current]:
            self._entries.pop(key, None)
