"""
Avatar storage layer: bounded checksum caches and cache invalidation.

The route keeps its own in-process checksum caches (never refreshed by the
manager) plus Valkey-backed ETag checksums. Both must be cleared on upload or
delete, or clients keep receiving ``304 Not Modified`` for the old avatar.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from .constants import _CHECKSUM_CACHE_TTL_SEC, _MAX_CHECKSUM_CACHE_ENTRIES

import utils.logger as logger


class _BoundedChecksumCache:
    """Thread-safe bounded LRU cache with TTL for checksum strings. (M5)"""

    def __init__(
        self,
        maxsize: int = _MAX_CHECKSUM_CACHE_ENTRIES,
        ttl: float = _CHECKSUM_CACHE_TTL_SEC,
    ) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._data: "OrderedDict[int, tuple[str, float]]" = OrderedDict()
        self._lock = threading.Lock()

    def _expired(self, ts: float, now: float) -> bool:
        return (now - ts) > self._ttl

    def _purge_expired(self, now: float) -> None:
        # Oldest-first; stop at first live entry (timestamps are not
        # strictly ordered after LRU touch, so fall back to full scan
        # only when the cache is at/over capacity).
        for key in list(self._data.keys()):
            try:
                _, ts = self._data[key]
            except KeyError:
                continue
            if self._expired(ts, now):
                self._data.pop(key, None)
            if len(self._data) <= self._maxsize:
                # Still scan the head for staleness when small; cheap.
                pass
        # Enforce hard cap (evict oldest first).
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def get(self, key: int, default: Optional[str] = None) -> Optional[str]:
        now = time.monotonic()
        with self._lock:
            try:
                value, ts = self._data[key]
            except KeyError:
                return default
            if self._expired(ts, now):
                self._data.pop(key, None)
                return default
            self._data.move_to_end(key)
            return value

    def __getitem__(self, key: int) -> Optional[str]:
        result = self.get(key)
        if result is None:
            # Distinguish missing from stored None (we never store None).
            with self._lock:
                if key not in self._data:
                    raise KeyError(key)
        return result

    def __setitem__(self, key: int, value: str) -> None:
        now = time.monotonic()
        with self._lock:
            if key in self._data:
                self._data.pop(key, None)
            self._data[key] = (value, now)
            if len(self._data) > self._maxsize:
                self._purge_expired(now)
                while len(self._data) > self._maxsize:
                    self._data.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, int):
            return False
        now = time.monotonic()
        with self._lock:
            try:
                _, ts = self._data[key]
            except KeyError:
                return False
            if self._expired(ts, now):
                self._data.pop(key, None)
                return False
            return True

    def pop(self, key: int, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            entry = self._data.pop(key, None)
        if entry is None:
            return default
        return entry[0]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            return len(self._data)


# Module-level singleton caches: {uid: checksum} and {sid: checksum}.
_avatar_checksum_cache = _BoundedChecksumCache()
_icon_checksum_cache = _BoundedChecksumCache()


def _cache_set_checksum(cache: Any, key: int, checksum: str) -> None:
    """Insert into a checksum cache, evicting the oldest entry at the cap."""
    if isinstance(cache, _BoundedChecksumCache):
        cache[key] = checksum
        return
    # Legacy plain-dict path (kept for backwards compatibility / tests).
    if len(cache) >= _MAX_CHECKSUM_CACHE_ENTRIES:
        # Evict the first-inserted entry (dicts preserve insertion order).
        try:
            oldest = next(iter(cache))
            cache.pop(oldest, None)
        except StopIteration:
            pass
    cache[key] = checksum


def _invalidate_user_avatar_cache(user_id: int) -> None:
    """Drop all cached avatar state so a re-upload/deletion is reflected.

    The route keeps its own in-process checksum cache (``_avatar_checksum_cache``)
    which is never refreshed by the manager, and the manager caches the ETag
    checksum in Valkey under ``user_avatar_checksum:{user_id}``. Both must be
    cleared or clients keep receiving ``304 Not Modified`` for the old avatar.
    """
    _avatar_checksum_cache.pop(user_id, None)
    try:
        from src.core.database import invalidate_pattern

        invalidate_pattern(f"user_avatar_checksum:{user_id}")
    except Exception as e:  # cache invalidation is best-effort
        logger.debug(
            f"Failed to invalidate user avatar checksum cache for {user_id}: {e}"
        )


def _invalidate_server_icon_cache(server_id: int) -> None:
    """Drop all cached server-icon state so a re-upload/deletion is reflected."""
    _icon_checksum_cache.pop(server_id, None)

    try:
        from src.core.database import invalidate_pattern

        invalidate_pattern(f"server_icon_checksum:{server_id}")
    except Exception as e:  # cache invalidation is best-effort
        logger.debug(
            f"Failed to invalidate server icon checksum cache for {server_id}: {e}"
        )


def invalidate_avatar_caches(user_id: int) -> None:
    """Invalidate both the local and Valkey avatar cache for a user."""
    _invalidate_user_avatar_cache(user_id)


def invalidate_icon_caches(server_id: int) -> None:
    """Invalidate both the local and Valkey icon cache for a server."""
    _invalidate_server_icon_cache(server_id)


__all__ = [
    "_BoundedChecksumCache",
    "_cache_set_checksum",
    "_avatar_checksum_cache",
    "_icon_checksum_cache",
    "_invalidate_user_avatar_cache",
    "_invalidate_server_icon_cache",
    "invalidate_avatar_caches",
    "invalidate_icon_caches",
]
