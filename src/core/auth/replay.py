"""Bounded replay/nonce guards for sensitive authentication flows.

A one-time credential (a rotated refresh token, a gateway ticket, a reset
nonce) must not be accepted twice. The stores here are deliberately bounded
in both entry count and lifetime: an attacker who can trigger many auth
attempts must not be able to grow process memory without limit, and old
entries must age out on their own. Only SHA-256 digests of keys are retained,
so a cache/introspection leak cannot recover a live token secret.
"""

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Dict


class BoundedReplayGuard:
    """Thread-safe bounded set of recently-seen one-time values (TTL + LRU).

    ``contains`` checks membership; ``add`` records a consumed value;
    ``consume`` does both atomically for flows that need a single gate.
    """

    def __init__(self, max_entries: int = 4096, ttl_seconds: int = 600) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl = max(1, int(ttl_seconds))
        self._entries: Dict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _digest(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _purge(self, now: float) -> None:
        """Drop expired entries (caller holds the lock)."""
        entries = self._entries
        while entries:
            key, expires = next(iter(entries.items()))
            if expires > now:
                break
            entries.pop(key, None)

    def _trim(self) -> None:
        entries = self._entries
        while len(entries) > self._max_entries:
            entries.pop(next(iter(entries)), None)

    def contains(self, key: str) -> bool:
        """Whether ``key`` was recorded recently and is still live."""
        digest = self._digest(key)
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            expires = self._entries.get(digest)
            if expires is None:
                return False
            # Refresh LRU position without extending the absolute TTL.
            self._entries.move_to_end(digest)
            return expires > now

    def add(self, key: str) -> None:
        """Record ``key`` as consumed for ``ttl_seconds``."""
        digest = self._digest(key)
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            self._entries[digest] = now + self._ttl
            self._entries.move_to_end(digest)
            self._trim()

    def consume(self, key: str) -> bool:
        """Atomically claim ``key``: True if fresh, False if replayed."""
        digest = self._digest(key)
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            if digest in self._entries:
                return False
            self._entries[digest] = now + self._ttl
            self._entries.move_to_end(digest)
            self._trim()
            return True

    def clear(self) -> None:
        """Drop all entries (used by tests and operator resets)."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Rotated session refresh tokens: bounded, process-local LRU guard. The
# database already guarantees the current token_hash is single-use; this
# guard additionally detects *reuse of a previously rotated* token so the
# session can be revoked as compromised rather than merely ignored.
refresh_replay_guard = BoundedReplayGuard(max_entries=4096, ttl_seconds=600)


__all__ = ["BoundedReplayGuard", "refresh_replay_guard"]
