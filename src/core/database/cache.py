"""
Cache module - Provides caching layer using Redis with fallback support.

This module provides decorator-based caching and manual cache operations
for frequently accessed data like user profiles, server info, and sessions.

Features:
    - Decorator-based caching (@cached)
    - Manual cache get/set/invalidate
    - Automatic key generation from function arguments
    - TTL support with configurable defaults
    - Graceful fallback when Redis is unavailable
    - Cache statistics and monitoring
"""

import json
import hashlib
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, cast
from functools import wraps

import utils.logger as logger

from .redis_client import (
    get_client,
    is_available,
    RedisOperationError,
    JsonSerializable,
)

# Type variable for generic return types
T = TypeVar("T", bound=JsonSerializable)

# Cache statistics
_cache_stats = {
    "hits": 0,
    "misses": 0,
    "errors": 0,
}


class CacheError(Exception):
    """Base exception for cache operations."""

    pass


def _generate_cache_key(prefix: str, *args, **kwargs) -> str:
    """
    Generate a cache key from function arguments.

    Args:
        prefix: Key prefix (usually function name).
        args: Positional arguments.
        kwargs: Keyword arguments.

    Returns:
        A unique cache key string.
    """
    # Build a string representation of arguments
    key_parts = [prefix]

    for arg in args:
        if isinstance(arg, (dict, list)):
            key_parts.append(
                f"{type(arg).__name__}:{hashlib.md5(json.dumps(arg, sort_keys=True).encode()).hexdigest()[:8]}"
            )
        else:
            key_parts.append(f"{type(arg).__name__}:{arg}")

    for k, v in sorted(kwargs.items()):
        if isinstance(v, (dict, list)):
            key_parts.append(
                f"{k}:{type(v).__name__}:{hashlib.md5(json.dumps(v, sort_keys=True).encode()).hexdigest()[:8]}"
            )
        else:
            key_parts.append(f"{k}:{type(v).__name__}:{v}")

    return ":".join(key_parts)


def cached(
    ttl: Optional[int] = None,
    prefix: Optional[str] = None,
    key_builder: Optional[Callable[..., str]] = None,
    skip_cache_if: Optional[Callable[..., bool]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to cache function results in Redis.

    Args:
        ttl: Time-to-live in seconds. Defaults to cache TTL from config.
        prefix: Custom key prefix. Defaults to function name.
        key_builder: Custom function to build cache key from arguments.
        skip_cache_if: Function that returns True to skip caching for this call.

    Usage:
        @cached(ttl=300)
        def get_user(user_id: int) -> dict:
            return db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

        @cached(ttl=60, prefix="server")
        def get_server_info(server_id: int) -> dict:
            return expensive_operation(server_id)

        @cached(key_builder=lambda user_id, **kw: f"user:{user_id}")
        def get_user_profile(user_id: int, include_stats: bool = False) -> dict:
            ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            global _cache_stats

            # Check if we should skip caching
            if skip_cache_if and skip_cache_if(*args, **kwargs):
                return func(*args, **kwargs)

            # Check if Redis is available
            client = get_client()
            if not client or not is_available():
                return func(*args, **kwargs)

            # Generate cache key
            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = _generate_cache_key(key_prefix, *args, **kwargs)

            # Try to get from cache
            try:
                cached_value = client.get_json(cache_key)
                if cached_value is not None:
                    _cache_stats["hits"] += 1
                    logger.info(
                        f"CACHE HIT: {cache_key} (total hits: {_cache_stats['hits']})"
                    )
                    return cast(T, cached_value)
            except RedisOperationError:
                _cache_stats["errors"] += 1
                # Fall through to execute function

            # Cache miss - execute function
            _cache_stats["misses"] += 1
            logger.info(
                f"CACHE MISS: {cache_key} (total misses: {_cache_stats['misses']})"
            )

            result = func(*args, **kwargs)

            # Store in cache
            try:
                cache_ttl = ttl if ttl is not None else client.ttl_cache
                client.set_json(
                    cache_key, cast(JsonSerializable, result), ttl=cache_ttl
                )
            except RedisOperationError as e:
                _cache_stats["errors"] += 1
                logger.warning(f"Failed to cache result for {cache_key}: {e}")

            return result

        # Add cache control methods to the wrapper
        wrapper_any = cast(Any, wrapper)
        wrapper_any.cache_key_prefix = (
            prefix or f"cache:{func.__module__}.{func.__name__}"
        )
        wrapper_any.invalidate = lambda *args, **kwargs: invalidate_cached(
            key_builder(*args, **kwargs)
            if key_builder
            else _generate_cache_key(wrapper_any.cache_key_prefix, *args, **kwargs)
        )

        return wrapper

    return decorator


def cache_get(key: str) -> Optional[Any]:
    """
    Get a value from cache.

    Args:
        key: Cache key.

    Returns:
        Cached value or None if not found.
    """
    global _cache_stats

    client = get_client()
    if not client or not is_available():
        return None

    try:
        value = client.get_json(key)
        if value is not None:
            _cache_stats["hits"] += 1
            logger.debug(f"Cache GET HIT: {key}")
        else:
            _cache_stats["misses"] += 1
            logger.debug(f"Cache GET MISS: {key}")
        return value
    except RedisOperationError as e:
        _cache_stats["errors"] += 1
        logger.warning(f"Cache GET failed for {key}: {e}")
        return None


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """
    Set a value in cache.

    Args:
        key: Cache key.
        value: Value to cache (must be JSON-serializable).
        ttl: Time-to-live in seconds.

    Returns:
        True if successful, False otherwise.
    """
    client = get_client()
    if not client or not is_available():
        return False

    try:
        cache_ttl = ttl if ttl is not None else client.ttl_cache
        client.set_json(key, value, ttl=cache_ttl)
        logger.debug(f"Cache SET: {key} (ttl={cache_ttl})")
        return True
    except RedisOperationError as e:
        _cache_stats["errors"] += 1
        logger.warning(f"Cache SET failed for {key}: {e}")
        return False


def cache_delete(key: str) -> bool:
    """
    Delete a value from cache.

    Args:
        key: Cache key.

    Returns:
        True if deleted, False otherwise.
    """
    client = get_client()
    if not client or not is_available():
        return False

    try:
        client.delete(key)
        logger.debug(f"Cache DELETE: {key}")
        return True
    except RedisOperationError as e:
        _cache_stats["errors"] += 1
        logger.warning(f"Cache DELETE failed for {key}: {e}")
        return False


def invalidate_cached(key: str) -> bool:
    """
    Invalidate a cached value (alias for cache_delete).

    Args:
        key: Cache key to invalidate.

    Returns:
        True if invalidated, False otherwise.
    """
    return cache_delete(key)


def invalidate_pattern(pattern: str) -> int:
    """
    Invalidate all cache keys matching a pattern.

    Args:
        pattern: Glob-style pattern (e.g., "user:*", "server:123:*").

    Returns:
        Number of keys invalidated.
    """
    client = get_client()
    if not client or not is_available():
        return 0

    try:
        keys = client.keys(pattern)
        if keys:
            count = client.delete(*keys)
            logger.debug(f"Cache INVALIDATE pattern '{pattern}': {count} keys")
            return count
        return 0
    except RedisOperationError as e:
        _cache_stats["errors"] += 1
        logger.warning(f"Cache INVALIDATE pattern failed for {pattern}: {e}")
        return 0


def cache_stats() -> Dict[str, int]:
    """
    Get cache statistics.

    Returns:
        Dict with hits, misses, errors counts.
    """
    return _cache_stats.copy()


def reset_cache_stats() -> None:
    """Reset cache statistics."""
    global _cache_stats
    _cache_stats = {"hits": 0, "misses": 0, "errors": 0}


def cache_health() -> Dict[str, Any]:
    """
    Get cache health information.

    Returns:
        Dict with cache health status.
    """
    client = get_client()

    result = {
        "available": is_available(),
        "stats": cache_stats(),
    }

    if client:
        result["redis"] = client.health_check()

    # Calculate hit rate
    total = _cache_stats["hits"] + _cache_stats["misses"]
    if total > 0:
        result["hit_rate"] = round(_cache_stats["hits"] / total * 100, 2)
    else:
        result["hit_rate"] = 0.0

    return result


# ==================== Session Cache Helpers ====================


def cache_session(
    session_id: str, user_id: int, data: Dict[str, Any], ttl: Optional[int] = None
) -> bool:
    """
    Cache a user session.

    Args:
        session_id: Unique session identifier.
        user_id: User ID.
        data: Session data.
        ttl: Session TTL in seconds.

    Returns:
        True if cached successfully.
    """
    client = get_client()
    if not client or not is_available():
        return False

    session_data = {
        "user_id": user_id,
        "created_at": time.time(),
        **data,
    }

    try:
        session_ttl = ttl if ttl is not None else client.ttl_session
        client.set_json(f"session:{session_id}", session_data, ttl=session_ttl)
        # Also add to user's session set
        client.sadd(f"user_sessions:{user_id}", session_id)
        logger.debug(f"Session cached: {session_id} for user {user_id}")
        return True
    except RedisOperationError as e:
        logger.warning(f"Failed to cache session {session_id}: {e}")
        return False


def get_cached_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a cached session.

    Args:
        session_id: Session identifier.

    Returns:
        Session data or None if not found.
    """
    return cache_get(f"session:{session_id}")


def invalidate_session(session_id: str, user_id: Optional[int] = None) -> bool:
    """
    Invalidate a session.

    Args:
        session_id: Session identifier.
        user_id: User ID (optional, for cleanup).

    Returns:
        True if invalidated.
    """
    client = get_client()
    if not client or not is_available():
        return False

    try:
        client.delete(f"session:{session_id}")
        if user_id:
            client.srem(f"user_sessions:{user_id}", session_id)
        logger.debug(f"Session invalidated: {session_id}")
        return True
    except RedisOperationError as e:
        logger.warning(f"Failed to invalidate session {session_id}: {e}")
        return False


def invalidate_user_sessions(user_id: int) -> int:
    """
    Invalidate all sessions for a user.

    Args:
        user_id: User ID.

    Returns:
        Number of sessions invalidated.
    """
    client = get_client()
    if not client or not is_available():
        return 0

    try:
        session_ids = client.smembers(f"user_sessions:{user_id}")
        if session_ids:
            keys_to_delete = [f"session:{sid}" for sid in session_ids]
            keys_to_delete.append(f"user_sessions:{user_id}")
            client.delete(*keys_to_delete)
            logger.debug(f"Invalidated {len(session_ids)} sessions for user {user_id}")
            return len(session_ids)
        return 0
    except RedisOperationError as e:
        logger.warning(f"Failed to invalidate sessions for user {user_id}: {e}")
        return 0


def invalidate_user_servers(user_id: int) -> int:
    """
    Invalidate the server list cache for a user.
    """
    return invalidate_pattern(f"servers:*:{user_id}*")


def invalidate_server(server_id: int) -> int:
    """
    Invalidate the server list cache for all users who might have this server in their list.
    Since we don't know all members here, we invalidate the entire servers cache.
    In a production environment, you might want to be more specific.
    """
    return invalidate_pattern("servers:*")


def invalidate_server_channels(server_id: int) -> int:
    """
    Invalidate the channel list cache for all users of a server.
    """
    return invalidate_pattern(f"channels:*:{server_id}*")


# ==================== Presence Cache Helpers ====================


def cache_presence(
    user_id: int, status: str, custom_status: Optional[str] = None
) -> bool:
    """
    Cache user presence/status.

    Args:
        user_id: User ID.
        status: Status string (online, idle, dnd, offline).
        custom_status: Optional custom status text.

    Returns:
        True if cached successfully.
    """
    client = get_client()
    if not client or not is_available():
        return False

    presence_data = {
        "status": status,
        "custom_status": custom_status,
        "updated_at": time.time(),
    }

    try:
        client.set_json(f"presence:{user_id}", presence_data, ttl=client.ttl_presence)
        return True
    except RedisOperationError as e:
        logger.warning(f"Failed to cache presence for user {user_id}: {e}")
        return False


def get_cached_presence(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get cached user presence.

    Args:
        user_id: User ID.

    Returns:
        Presence data or None if not found.
    """
    return cache_get(f"presence:{user_id}")


def get_bulk_presence(user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """
    Get presence for multiple users.

    Args:
        user_ids: List of user IDs.

    Returns:
        Dict mapping user_id to presence data.
    """
    result = {}
    for user_id in user_ids:
        presence = get_cached_presence(user_id)
        if presence:
            result[user_id] = presence
    return result


# ==================== Rate Limiting Helpers ====================


# Global dictionary for in-memory rate limiting when Redis is unavailable
_mem_rate_limits: Dict[str, List[float]] = {}

def check_rate_limit(key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
    """
    Check if a rate limit has been exceeded.

    Args:
        key: Rate limit key (e.g., "ratelimit:user:123:messages").
        limit: Maximum number of requests allowed.
        window_seconds: Time window in seconds.

    Returns:
        Tuple of (allowed: bool, remaining: int).
    """
    client = get_client()
    if not client or not is_available():
        # Fallback to in-memory cache if Redis is unavailable (important for tests)
        global _mem_rate_limits
        full_key = f"ratelimit:{key}"
        
        # Simple sliding window implementation in memory
        now = time.time()
        timestamps = _mem_rate_limits.get(full_key, [])
        # Filter out old timestamps
        timestamps = [ts for ts in timestamps if ts > now - window_seconds]
        
        if len(timestamps) < limit:
            timestamps.append(now)
            _mem_rate_limits[full_key] = timestamps
            return True, limit - len(timestamps)
        else:
            _mem_rate_limits[full_key] = timestamps
            return False, 0

    full_key = f"ratelimit:{key}"

    try:
        current = client.incr(full_key)

        # Set expiry on first request
        if current == 1:
            client.expire(full_key, window_seconds)

        remaining = max(0, limit - current)
        allowed = current <= limit

        return allowed, remaining
    except RedisOperationError as e:
        logger.warning(f"Rate limit check failed for {key}: {e}")
        return True, limit


def reset_rate_limit(key: str) -> bool:
    """
    Reset a rate limit counter.

    Args:
        key: Rate limit key.

    Returns:
        True if reset successfully.
    """
    return cache_delete(f"ratelimit:{key}")
