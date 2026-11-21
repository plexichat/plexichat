"""Bounded, role-version-keyed cache of channel-scoped permission decisions.

Why this exists
---------------
A single request that hydrates a channel's message list, resolves an event's
recipient audience, or fans out notifications performs the *same*
``has_permission(user, server, permission, channel)`` check many times: once to
gate ``get_channel`` (``channels.view``), once for ``messages.read``, again for
every poll-bearing message hydrated through ``get_poll``/``get_results``, and
again on each page of a paged read. Each check re-enters the permission resolver
and, on a cold permission snapshot, re-reads the owner, member roles and
channel overrides. The existing ``@cached(ttl=10, prefix="has_perm")`` tier is
TTL- and pattern-invalidated, which is slow to converge on an RBAC change and
does not express the decision's true identity.

This module memoizes the **boolean decision** for a channel-scoped permission
check for a short, version-bounded epoch. Its key is the complete decision
identity: ``(user, channel, permission, role-version)`` where ``role-version``
is a per-server/per-user epoch that is bumped by every role, membership,
override or ownership mutation. Consequences:

* **Bounded** — a fixed-size LRU with a hard TTL; retained decisions cannot grow
  without limit and cannot outlive the TTL.
* **Immediately invalidated** — a role/override/membership change bumps the
  relevant epoch, so every decision computed under the old permissions becomes
  unreachable at once (no stale grant can survive a revocation).
* **Keyed by the complete identity** — user, channel, permission and version are
  all in the key; a decision for one user/channel/permission can never be served
  for another, and a stale version can never be served for a fresh one.
* **Fail-closed** — only explicit booleans produced by the authoritative
  resolver are stored. A miss, an expired entry, or any cache error returns
  ``None`` so the caller recomputes from the source of truth. Cache failures
  never synthesize or widen a grant.

The cache holds *decisions*, never permission sets, and is shared process-wide
(it is keyed only by request-derived values, never by a credential or token).
"""

from __future__ import annotations

import heapq
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

#: Distinct (user, channel, permission) triples touched by one request are few;
#: 2048 entries is ample for a busy process while keeping the LRU tightly bound.
DEFAULT_MAX_ENTRIES = 2048

#: Defence in depth on top of version invalidation: even if an invalidation hook
#: is missed, no decision can be replayed past this window. Matches the existing
#: ``has_perm`` tier so time-based changes (timeout expiry) propagate on the
#: same cadence as before.
DEFAULT_TTL_SECONDS = 10.0

_MISSING = object()

# (user_id, channel_id, permission, role_version)
# role_version is (server_epoch, user_epoch); a channel id is globally unique
# and implies its server, so no separate server component is needed in the key.
_Key = Tuple[int, int, str, Tuple[int, int]]


class ChannelAccessCache:
    """Bounded LRU of channel-scoped permission decisions keyed by identity+version."""

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl = float(ttl_seconds)
        if self._ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._clock = clock or time.monotonic
        self._entries: "OrderedDict[_Key, bool]" = OrderedDict()
        self._expires: Dict[_Key, float] = {}
        # Min-heap of (expiry, sequence, key) ordering decisions by deadline.
        # Reclaiming expired entries touches only those whose deadline actually
        # passed (O(k log n) for k expired), instead of scanning every resident
        # decision on every write. ``sequence`` breaks expiry ties so keys are
        # never compared with one another. Stale tuples left by a re-put or an
        # LRU eviction are discarded when their recorded deadline no longer
        # matches the live entry's (see ``_prune_locked``).
        self._expiry_heap: List[Tuple[float, int, _Key]] = []
        self._seq = 0
        # Epochs start at 0 and only ever increase.
        self._server_epochs: Dict[int, int] = {}
        self._user_epochs: Dict[Tuple[int, int], int] = {}
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._lock = threading.RLock()

    # -- versioning ---------------------------------------------------------
    def version(self, server_id: int, user_id: int) -> Tuple[int, int]:
        """Current ``(server_epoch, user_epoch)`` for this scope.

        Any RBAC/ownership mutation in the server bumps ``server_epoch``;
        a mutation targeting a single member bumps ``user_epoch`` as well.
        """
        sid, uid = int(server_id), int(user_id)
        with self._lock:
            return (
                self._server_epochs.get(sid, 0),
                self._user_epochs.get((sid, uid), 0),
            )

    def bump(self, server_id: int, user_id: Optional[int] = None) -> None:
        """Invalidate decisions by advancing the relevant epoch.

        Call from every role / membership / override / ownership mutation.
        Old-version entries become unreachable immediately; they are pruned
        lazily by TTL/LRU, so this stays O(1) on the hot mutation path.
        """
        sid = int(server_id)
        with self._lock:
            self._server_epochs[sid] = self._server_epochs.get(sid, 0) + 1
            if user_id is not None:
                key = (sid, int(user_id))
                self._user_epochs[key] = self._user_epochs.get(key, 0) + 1
        # Every RBAC mutation funnels through here, so this is the one place
        # that can also evict response caches whose body was computed behind
        # an authorization gate but carries no RBAC version of its own. The
        # per-channel GET response (``channel_api``) is the notable gap: its
        # cached loader ran get_channel (membership + channels.view), and the
        # decorator replayed the warm 200 without re-checking that gate. No
        # role/member/override mutation cleared that namespace, so a viewer
        # who lost access kept a valid channel response for the full TTL.
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern("channel_api:*")
            # The per-viewer channel list (``server_channels`` at the manager
            # tier and ``server_channels_api`` at the HTTP tier) is the same
            # class of gap: its cached body is the set of channels that passed
            # the ``channels.view`` gate for that viewer. Role/membership/
            # ownership mutations clear those namespaces through their own
            # helpers, but a channel-override revoke only bumped the epoch,
            # leaving a warm list that kept granting a now-revoked view for
            # the whole 30s TTL. Evict them here so every RBAC mutation
            # retires the bundled verdicts.
            invalidate_pattern("server_channels:*")
            invalidate_pattern("server_channels_api:*")
        except Exception:  # pragma: no cover - accelerator cache only
            pass

    # -- decision cache -----------------------------------------------------
    def get(
        self,
        server_id: int,
        user_id: int,
        channel_id: int,
        permission: str,
    ) -> Optional[bool]:
        """Return the cached decision for the *current* role-version, or ``None``.

        The version is derived internally from ``(server_id, user_id)`` on every
        call rather than supplied by the caller. A stale version can therefore
        never be presented to reuse an old grant: after a bump the key simply
        changes to the new epoch and the old entry is unreachable. ``False`` is
        a legitimate decision returned as-is; only ``None`` signals a miss so
        the caller recomputes from the authoritative source.
        """
        version = self.version(server_id, user_id)
        key = (int(user_id), int(channel_id), str(permission), version)
        now = self._clock()
        with self._lock:
            value = self._entries.get(key, _MISSING)
            if value is _MISSING:
                self._misses += 1
                return None
            expiry = self._expires.get(key, 0.0)
            if expiry <= now:
                self._entries.pop(key, None)
                self._expires.pop(key, None)
                self._misses += 1
                return None
            self._hits += 1
            self._entries.move_to_end(key)
            return value  # type: ignore[return-value]

    def put(
        self,
        server_id: int,
        user_id: int,
        channel_id: int,
        permission: str,
        decision: bool,
    ) -> None:
        """Store an authoritative boolean decision under the current version."""
        version = self.version(server_id, user_id)
        key = (int(user_id), int(channel_id), str(permission), version)
        stored = bool(decision)
        expiry = self._clock() + self._ttl
        with self._lock:
            self._entries[key] = stored
            self._expires[key] = expiry
            heapq.heappush(self._expiry_heap, (expiry, self._seq, key))
            self._seq += 1
            self._entries.move_to_end(key)
            self._prune_locked()
            self._puts += 1

    def _prune_locked(self) -> None:
        now = self._clock()
        # The heap is ordered by deadline, so this loop runs once per entry
        # that actually expired since the last write rather than once for
        # every live entry. A stale tuple (from a re-put or an LRU eviction of
        # the same key) is ignored when its deadline no longer matches the
        # live entry's, so it can never evict a fresh decision. Expiry checks
        # in ``get`` remain the authoritative fail-closed guard: an entry
        # whose heap tuple has not been popped yet is still treated as a miss
        # the moment its deadline passes.
        heap = self._expiry_heap
        while heap and heap[0][0] <= now:
            expiry, _seq, key = heapq.heappop(heap)
            if self._expires.get(key) == expiry:
                self._entries.pop(key, None)
                self._expires.pop(key, None)
        # Hard size ceiling; amortized O(1) per write.
        while len(self._entries) > self._max_entries:
            evicted, _ = self._entries.popitem(last=False)
            self._expires.pop(evicted, None)

    def clear(self) -> None:
        """Drop every decision and reset counters (test isolation / flush).

        Epochs are kept: a clear is a cache flush, not an RBAC change, so
        decisions keyed to the current permissions remain valid if recomputed.
        """
        with self._lock:
            self._entries.clear()
            self._expires.clear()
            self._expiry_heap.clear()
            self._hits = 0
            self._misses = 0
            self._puts = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def stats(self) -> Dict[str, int]:
        """Hit/miss/put counters and current size, for tests/observability."""
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "puts": self._puts,
            }


# One process-wide memo. It is keyed only by request-derived values and by a
# monotonically increasing RBAC epoch, never by a credential, so sharing it
# across requests/managers cannot leak one viewer's decision to another.
_channel_access_cache = ChannelAccessCache()


def get_channel_access_cache() -> ChannelAccessCache:
    return _channel_access_cache
