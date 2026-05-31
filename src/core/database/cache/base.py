"""
Base types and in-memory cache fallback for the cache module.

Provides CacheError exception and in-memory cache dictionary
used as fallback when Redis is unavailable.
"""

import time
from typing import Any, Dict, Optional, Tuple

# Cache storage
_mem_cache: Dict[str, Tuple[float, Any]] = {}
_MEM_CACHE_MAX_SIZE = 1000


class CacheError(Exception):
    """Base exception for cache operations."""

    pass


def mem_cache_get(key: str) -> Optional[Any]:
    """Get a value from the in-memory cache, handling expiry."""
    global _mem_cache
    if key in _mem_cache:
        expiry, value = _mem_cache[key]
        if expiry > time.time():
            return value
        del _mem_cache[key]
    return None


def mem_cache_set(key: str, value: Any, ttl: int) -> None:
    """Set a value in the in-memory cache with TTL."""
    global _mem_cache
    if len(_mem_cache) >= _MEM_CACHE_MAX_SIZE:
        keys = list(_mem_cache.keys())
        for k in keys[: _MEM_CACHE_MAX_SIZE // 2]:
            del _mem_cache[k]
    _mem_cache[key] = (time.time() + ttl, value)


def mem_cache_clear_pattern(pattern: str) -> int:
    """Clear in-memory cache entries matching a pattern."""
    import fnmatch

    global _mem_cache
    keys = [k for k in list(_mem_cache.keys()) if fnmatch.fnmatch(str(k), pattern)]
    for k in keys:
        del _mem_cache[k]
    return len(keys)
