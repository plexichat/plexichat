"""
Base types and in-memory cache fallback for the cache module.

Provides CacheError exception and in-memory cache dictionary
used as fallback when Valkey is unavailable.
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import utils.config as config

# Cache storage
_mem_cache: Dict[str, Tuple[float, Any]] = {}
_MEM_CACHE_MAX_SIZE = 1000

# Guards every read-modify-write on _mem_cache. The cached decorator and
# cache invalidation run on unrelated threads (request handlers, SFU/event
# callbacks, background reapers); without this lock two threads racing in
# mem_cache_clear_pattern / the eviction loop snapshot the same key list
# and the second ``del`` raises KeyError (seen as
# ``KeyError: 'has_perm:...'`` under parallel test load).
_mem_cache_lock = threading.Lock()

# Default TTLs used by the in-memory fallback tier. Mirror valkey's
# ``ttl.*`` so both tiers behave identically.
_DEFAULT_TTLS = {"cache": 60, "presence": 300, "session": 1800}
_DEFAULT_CACHE_TTL = _DEFAULT_TTLS["cache"]


def default_ttl(kind: str = "cache") -> int:
    """Effective default TTL for ``kind`` (cache/presence/session), honoring
    the configured ``valkey.ttl.<kind>`` even when Valkey is not connected.
    Keeps the in-memory fallback and the Valkey tier on the same expiry."""
    try:
        valkey_cfg = config.get("valkey") or config.get("redis") or {}
        ttl = (valkey_cfg.get("ttl", {}) or {}).get(kind, _DEFAULT_TTLS.get(kind, 60))
        return int(ttl)
    except (TypeError, ValueError):
        return _DEFAULT_TTLS.get(kind, 60)


def default_cache_ttl() -> int:
    """Effective default cache TTL (see ``default_ttl``)."""
    return default_ttl("cache")


class CacheError(Exception):
    """Base exception for cache operations."""

    pass


def mem_cache_get(key: str) -> Optional[Any]:
    """Get a value from the in-memory cache, handling expiry."""
    global _mem_cache
    with _mem_cache_lock:
        try:
            expiry, value = _mem_cache[key]
        except KeyError:
            return None
        if expiry > time.time():
            return value
        _mem_cache.pop(key, None)
    return None


def mem_cache_set(key: str, value: Any, ttl: int) -> None:
    """Set a value in the in-memory cache with TTL."""
    global _mem_cache
    with _mem_cache_lock:
        if len(_mem_cache) >= _MEM_CACHE_MAX_SIZE:
            keys = list(_mem_cache.keys())
            for k in keys[: _MEM_CACHE_MAX_SIZE // 2]:
                _mem_cache.pop(k, None)
        _mem_cache[key] = (time.time() + ttl, value)


def mem_cache_delete(key: str) -> bool:
    """Delete a key from the in-memory cache."""
    global _mem_cache
    with _mem_cache_lock:
        if key in _mem_cache:
            del _mem_cache[key]
            return True
        return False


def mem_cache_clear_pattern(pattern: str) -> int:
    """Clear in-memory cache entries matching a pattern."""
    global _mem_cache
    import fnmatch

    with _mem_cache_lock:
        keys = [k for k in _mem_cache.keys() if fnmatch.fnmatch(str(k), pattern)]
        for k in keys:
            _mem_cache.pop(k, None)
        return len(keys)


def mem_cache_matching_keys(pattern: str) -> List[str]:
    """Return in-memory cache keys matching a glob ``pattern``."""
    import fnmatch

    with _mem_cache_lock:
        return [k for k in list(_mem_cache.keys()) if fnmatch.fnmatch(str(k), pattern)]
