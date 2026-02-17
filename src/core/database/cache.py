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
import inspect
import dataclasses
import fnmatch
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Protocol, Tuple, Union, cast
from functools import wraps
import os

try:
    import diskcache
    _DISKCACHE_AVAILABLE = True
except ImportError:
    diskcache = None
    _DISKCACHE_AVAILABLE = False

import utils.logger as logger

from .redis_client import (
    get_client,
    is_available,
    RedisOperationError,
    JsonSerializable,
)

class _DiskCacheLike(Protocol):
    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any, expire: Optional[int] = None) -> Any: ...
    def pop(self, key: str, default: Any = None) -> Any: ...
    def keys(self, *args, **kwargs) -> Iterable[Any]: ...
    def iterkeys(self, *args, **kwargs) -> Iterable[Any]: ...

# Cache statistics
_cache_stats = {
    "hits": 0,
    "misses": 0,
    "errors": 0,
}

# Simple in-memory fallback cache (or diskcache if available)
if _DISKCACHE_AVAILABLE and diskcache is not None:
    cache_dir = os.path.join(
        os.path.expanduser("~"), ".plexichat", "cache", "local_cache"
    )
    try:
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        _mem_cache: Union[_DiskCacheLike, Dict[str, Tuple[float, Any]]] = cast(
            _DiskCacheLike, diskcache.Cache(cache_dir)
        )
        _mem_cache_is_diskcache = True
        logger.info(f"Initialized DiskCache at {cache_dir}")
    except Exception as e:
        if getattr(logger, "_setup_called", False):
            logger.warning(f"Failed to initialize DiskCache, using memory cache: {e}")
        _mem_cache = {}
        _mem_cache_is_diskcache = False
else:
    _mem_cache = {}
    _mem_cache_is_diskcache = False
_MEM_CACHE_MAX_SIZE = 1000

def _mem_cache_get(key: str) -> Optional[Any]:
    if _mem_cache_is_diskcache:
        # DiskCache handles expiry automatically
        # It returns None if key is missing or expired
        return cast(_DiskCacheLike, _mem_cache).get(key)
    else:
        mem_cache = cast(Dict[str, Tuple[float, Any]], _mem_cache)
        if key in mem_cache:
            expiry, value = mem_cache[key]
            if expiry > time.time():
                return value
            del mem_cache[key]
        return None

def _mem_cache_set(key: str, value: Any, ttl: int):
    if _mem_cache_is_diskcache:
        # DiskCache handles expiry (ttl in seconds)
        cast(_DiskCacheLike, _mem_cache).set(key, value, expire=ttl)
    else:
        mem_cache = cast(Dict[str, Tuple[float, Any]], _mem_cache)
        if len(mem_cache) >= _MEM_CACHE_MAX_SIZE:
            # Very simple eviction: clear half the cache if it gets too big
            keys = list(mem_cache.keys())
            for k in keys[:_MEM_CACHE_MAX_SIZE // 2]:
                del mem_cache[k]
        mem_cache[key] = (time.time() + ttl, value)

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

    def process_val(val):
        if hasattr(val, "__class__"):
            class_name = val.__class__.__name__
            if class_name in ("TokenInfo", "User"):
                # Only use user_id to avoid cache fragmentation by session/expiry
                uid = getattr(val, "user_id", None) or getattr(val, "id", "unknown")
                return f"{class_name}:{uid}"
            
            # Check for core managers and repositories by looking at the module or base classes
            # This avoids including instance memory addresses in cache keys
            from src.core.base import BaseManager
            try:
                # We use a broad check for anything that looks like a Repository or Manager
                # to ensure stable keys for all core components
                if isinstance(val, BaseManager) or class_name.endswith(("Repository", "Service", "Manager")):
                    return f"Core:{class_name}"
            except Exception:
                pass
                
        if isinstance(val, (dict, list)):
            return f"{type(val).__name__}:{hashlib.md5(json.dumps(val, sort_keys=True).encode()).hexdigest()[:8]}"
        return f"{type(val).__name__}:{val}"

    for arg in args:
        key_parts.append(process_val(arg))

    for k, v in sorted(kwargs.items()):
        key_parts.append(f"{k}:{process_val(v)}")

    return ":".join(key_parts)


def _ensure_serializable(obj: Any) -> Any:
    """Ensure an object is JSON serializable, recursively converting complex types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    
    if isinstance(obj, Enum):
        return obj.value
        
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        data = _ensure_serializable(dataclasses.asdict(obj))
        if isinstance(data, dict):
            data["__type__"] = f"{obj.__class__.__module__}.{obj.__class__.__name__}"
        return data
        
    if isinstance(obj, (list, tuple, set)):
        return [_ensure_serializable(item) for item in obj]
        
    if isinstance(obj, dict):
        return {str(k): _ensure_serializable(v) for k, v in obj.items()}
    
    # Handle Pydantic models (common in API responses)
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        data = _ensure_serializable(model_dump())
        if isinstance(data, dict):
            data["__type__"] = f"{obj.__class__.__module__}.{obj.__class__.__name__}"  # type: ignore
        return data
    dict_method = getattr(obj, "dict", None)
    if callable(dict_method):
        data = _ensure_serializable(dict_method())
        if isinstance(data, dict):
            data["__type__"] = f"{obj.__class__.__module__}.{obj.__class__.__name__}"  # type: ignore
        return data
        
    # Last resort fallback for custom types that might behave like strings (e.g. SnowflakeID)
    if hasattr(obj, "__str__") and not isinstance(obj, (dict, list, tuple)):
        return str(obj)
        
    return obj

# Type registry for reconstruction
_TYPE_REGISTRY = {}

def _get_type_from_name(type_name: str) -> Optional[type]:
    """Dynamically load and cache types for reconstruction."""
    if type_name in _TYPE_REGISTRY:
        return _TYPE_REGISTRY[type_name]
    
    try:
        module_path, class_name = type_name.rsplit(".", 1)
        import importlib
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        _TYPE_REGISTRY[type_name] = cls
        return cls
    except Exception:
        return None

def _reconstruct_object(data: Any) -> Any:
    """Recursively reconstruct objects from dictionaries using __type__ hints."""
    if isinstance(data, list):
        return [_reconstruct_object(item) for item in data]

    if isinstance(data, dict):
        if "__type__" in data:
            data_copy = dict(data)
            type_name = data_copy.pop("__type__")
            cls = _get_type_from_name(type_name)
            
            if not cls:
                return data_copy
                
            # Reconstruct children of this object
            reconstructed_params = {
                k: _reconstruct_object(v) for k, v in data_copy.items()
            }
            
            try:
                if issubclass(cls, Enum):
                    return cls(reconstructed_params)
                return cls(**reconstructed_params)
            except Exception as e:
                logger.debug(f"Failed to reconstruct {type_name}: {e}")
                return reconstructed_params
        else:
            # Plain dict, but recurse into values
            return {k: _reconstruct_object(v) for k, v in data.items()}
            
    return data

def cached(
    ttl: Optional[int] = None,
    prefix: Optional[str] = None,
    key_builder: Optional[Callable[..., str]] = None,
    skip_cache_if: Optional[Callable[..., bool]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to cache function results in Redis.

    Args:
        ttl: Time-to-live in seconds. Defaults to cache TTL from config.
        prefix: Custom key prefix. Defaults to function name.
        key_builder: Custom function to build cache key from arguments.
        skip_cache_if: Function that returns True to skip caching for this call.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_async = inspect.iscoroutinefunction(func)

        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            global _cache_stats

            # Check if we should skip caching
            if skip_cache_if and skip_cache_if(*args, **kwargs):
                async_func = cast(Callable[..., Awaitable[Any]], func)
                return await async_func(*args, **kwargs)

            # Check if Redis is available
            client = get_client()
            redis_ready = client is not None and is_available()

            # Generate cache key
            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = _generate_cache_key(key_prefix, *args, **kwargs)

            # Try to get from cache (Redis then Memory)
            if redis_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(
                            f"CACHE HIT (Redis): {cache_key} (total hits: {_cache_stats['hits']})"
                        )
                        return _reconstruct_object(cached_value)
                except RedisOperationError:
                    _cache_stats["errors"] += 1
            else:
                cached_value = _mem_cache_get(cache_key)
                if cached_value is not None:
                    _cache_stats["hits"] += 1
                    logger.debug(
                        f"CACHE HIT (Memory): {cache_key} (total hits: {_cache_stats['hits']})"
                    )
                    return _reconstruct_object(cached_value)

            # Cache miss - execute function
            _cache_stats["misses"] += 1
            logger.debug(
                f"CACHE MISS: {cache_key} (total misses: {_cache_stats['misses']})"
            )

            async_func = cast(Callable[..., Awaitable[Any]], func)
            result = await async_func(*args, **kwargs)
            serializable_result = _ensure_serializable(result)

            # Store in cache (Redis and/or Memory)
            cache_ttl = ttl if ttl is not None else 300
            if redis_ready and client is not None:
                cache_ttl = ttl if ttl is not None else client.ttl_cache
            
            if redis_ready:
                assert client is not None
                try:
                    client.set_json(
                        cache_key, cast(JsonSerializable, serializable_result), ttl=cache_ttl
                    )
                except RedisOperationError as e:
                    _cache_stats["errors"] += 1
                    logger.warning(f"Failed to cache result for {cache_key}: {e}")
            
            # Always store in memory as well for fastest possible second-hit or as fallback
            _mem_cache_set(cache_key, serializable_result, cache_ttl)

            return result

        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            global _cache_stats

            # Check if we should skip caching
            if skip_cache_if and skip_cache_if(*args, **kwargs):
                return func(*args, **kwargs)

            # Check if Redis is available
            client = get_client()
            redis_ready = client is not None and is_available()

            # Generate cache key
            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = _generate_cache_key(key_prefix, *args, **kwargs)

            # Try to get from cache (Redis then Memory)
            if redis_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(
                            f"CACHE HIT (Redis): {cache_key} (total hits: {_cache_stats['hits']})"
                        )
                        return _reconstruct_object(cached_value)
                except RedisOperationError:
                    _cache_stats["errors"] += 1
            else:
                cached_value = _mem_cache_get(cache_key)
                if cached_value is not None:
                    _cache_stats["hits"] += 1
                    logger.debug(
                        f"CACHE HIT (Memory): {cache_key} (total hits: {_cache_stats['hits']})"
                    )
                    return _reconstruct_object(cached_value)

            # Cache miss - execute function
            _cache_stats["misses"] += 1
            logger.debug(
                f"CACHE MISS: {cache_key} (total misses: {_cache_stats['misses']})"
            )

            result = func(*args, **kwargs)
            serializable_result = _ensure_serializable(result)

            # Store in cache (Redis and/or Memory)
            cache_ttl = ttl if ttl is not None else 300
            if redis_ready and client is not None:
                cache_ttl = ttl if ttl is not None else client.ttl_cache
            
            if redis_ready:
                assert client is not None
                try:
                    client.set_json(
                        cache_key, cast(JsonSerializable, serializable_result), ttl=cache_ttl
                    )
                except RedisOperationError as e:
                    _cache_stats["errors"] += 1
                    logger.warning(f"Failed to cache result for {cache_key}: {e}")
            
            # Always store in memory as well for fastest possible second-hit or as fallback
            _mem_cache_set(cache_key, serializable_result, cache_ttl)

            return result

        final_wrapper = async_wrapper if is_async else wrapper

        # Add cache control methods to the wrapper
        wrapper_any = cast(Any, final_wrapper)
        wrapper_any.cache_key_prefix = (
            prefix or f"cache:{func.__module__}.{func.__name__}"
        )
        wrapper_any.invalidate = lambda *args, **kwargs: invalidate_cached(
            key_builder(*args, **kwargs)
            if key_builder
            else _generate_cache_key(wrapper_any.cache_key_prefix, *args, **kwargs)
        )

        return final_wrapper

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
            return _reconstruct_object(value)
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
        value: Value to cache (must be JSON-serializable or a supported complex type).
        ttl: Time-to-live in seconds.

    Returns:
        True if successful, False otherwise.
    """
    client = get_client()
    if not client or not is_available():
        return False

    try:
        cache_ttl = ttl if ttl is not None else client.ttl_cache
        serializable_value = _ensure_serializable(value)
        client.set_json(key, serializable_value, ttl=cache_ttl)
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
    # Always clear in-memory cache entries that match the pattern
    # Wrap keys() in list() to handle both dict and DiskCache (which returns iterator)
    if _mem_cache_is_diskcache:
        # Limit the number of keys we check to avoid performance hit on huge caches
        # For diskcache, iterating all keys can be slow, but for local cache usage it is acceptable
        mem_keys = []
        for k in list(cast(_DiskCacheLike, _mem_cache).iterkeys()):
            # Handle potential bytes keys from diskcache
            k_str = k.decode('utf-8') if isinstance(k, bytes) else str(k)
            if fnmatch.fnmatch(k_str, pattern):
                mem_keys.append(k)
    else:
        mem_keys = [
            k
            for k in list(cast(Dict[str, Tuple[float, Any]], _mem_cache).keys())
            if fnmatch.fnmatch(str(k), pattern)
        ]
        
    for k in mem_keys:
        # pop works for both dict and DiskCache
        _mem_cache.pop(k, None)

    client = get_client()
    if not client or not is_available():
        return len(mem_keys)

    try:
        # get_client() returns a RedisClient instance from redis_client.py
        # RedisClient.keys(pattern) returns keys WITHOUT the prefix.
        keys = client.keys(pattern)
        if keys:
            # RedisClient.delete(*keys) expects keys WITHOUT prefix (it adds it itself)
            count = client.delete(*keys)
            logger.debug(f"Cache INVALIDATE pattern '{pattern}': {count} keys from Redis, {len(mem_keys)} from Memory")
            return count + len(mem_keys)
        return len(mem_keys)
    except RedisOperationError as e:
        _cache_stats["errors"] += 1
        logger.warning(f"Cache INVALIDATE pattern failed for {pattern}: {e}")
        return len(mem_keys)


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

        # Cleanup memory leak: If dictionary gets too large, purge all expired keys
        if len(_mem_rate_limits) > 5000:
            logger.debug("Cleaning up in-memory rate limit cache")
            # Create a copy of keys to iterate while deleting
            for k in list(_mem_rate_limits.keys()):
                # If the newest timestamp is older than max window (approx), delete
                if _mem_rate_limits[k] and _mem_rate_limits[k][-1] < now - 3600:
                    del _mem_rate_limits[k]

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
