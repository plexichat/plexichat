"""
Direct cache operations for the cache module.

Provides cache_get, cache_set, cache_delete, invalidate_pattern functions.
"""

from typing import Any, Dict, List, Optional, Tuple, cast

import utils.logger as logger
from ..redis_client import (
    get_client,
    is_available,
    RedisOperationError,
    JsonSerializable,
)
from .base import mem_cache_get, mem_cache_set, mem_cache_clear_pattern
from .serialization import ensure_serializable, reconstruct_object
from .decorators import get_cache_stats


def cache_get(key: str) -> Optional[Any]:
    """Get a value from cache."""
    stats = get_cache_stats()
    client = get_client()
    if not client or not is_available():
        return None

    try:
        value = client.get_json(key)
        if value is not None:
            stats["hits"] += 1
            logger.debug(f"Cache GET HIT: {key}")
            return reconstruct_object(value)
        else:
            stats["misses"] += 1
            logger.debug(f"Cache GET MISS: {key}")
        return value
    except RedisOperationError as e:
        stats["errors"] += 1
        logger.warning(f"Cache GET failed for {key}: {e}")
        return None


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Set a value in cache."""
    client = get_client()
    if not client or not is_available():
        return False

    try:
        cache_ttl = ttl if ttl is not None else client.ttl_cache
        serializable_value = ensure_serializable(value)
        client.set_json(key, serializable_value, ttl=cache_ttl)
        logger.debug(f"Cache SET: {key} (ttl={cache_ttl})")
        return True
    except RedisOperationError as e:
        stats = get_cache_stats()
        stats["errors"] += 1
        logger.warning(f"Cache SET failed for {key}: {e}")
        return False


def cache_delete(key: str) -> bool:
    """Delete a value from cache."""
    client = get_client()
    if not client or not is_available():
        return False

    try:
        client.delete(key)
        logger.debug(f"Cache DELETE: {key}")
        return True
    except RedisOperationError as e:
        stats = get_cache_stats()
        stats["errors"] += 1
        logger.warning(f"Cache DELETE failed for {key}: {e}")
        return False


def invalidate_cached(key: str) -> bool:
    """Invalidate a cached value (alias for cache_delete)."""
    return cache_delete(key)


def invalidate_pattern(pattern: str) -> int:
    """Invalidate all cache keys matching a pattern."""
    mem_count = mem_cache_clear_pattern(pattern)

    client = get_client()
    if not client or not is_available():
        return mem_count

    try:
        keys = client.keys(pattern)
        if keys:
            count = client.delete(*keys)
            logger.debug(
                f"Cache INVALIDATE pattern '{pattern}': {count} keys from Redis, {mem_count} from Memory"
            )
            return count + mem_count
        return mem_count
    except RedisOperationError as e:
        stats = get_cache_stats()
        stats["errors"] += 1
        logger.warning(f"Cache INVALIDATE pattern failed for {pattern}: {e}")
        return mem_count
