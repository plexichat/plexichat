"""
Decorator-based caching with Redis and in-memory fallback.

Provides the @cached decorator that supports both sync and async functions.
"""

import time
import inspect
from typing import (
    Any,
    Awaitable,
    Callable,
    Optional,
    cast,
)
from functools import wraps

import utils.logger as logger
from ..redis_client import (
    get_client,
    is_available,
    RedisOperationError,
    JsonSerializable,
)
from .base import mem_cache_get, mem_cache_set
from .serialization import generate_cache_key, ensure_serializable, reconstruct_object

_cache_stats: dict = {"hits": 0, "misses": 0, "errors": 0}


def cached(
    ttl: Optional[int] = None,
    prefix: Optional[str] = None,
    key_builder: Optional[Callable[..., str]] = None,
    skip_cache_if: Optional[Callable[..., bool]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to cache function results in Redis with in-memory fallback.

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

            if skip_cache_if and skip_cache_if(*args, **kwargs):
                async_func = cast(Callable[..., Awaitable[Any]], func)
                return await async_func(*args, **kwargs)

            client = get_client()
            redis_ready = client is not None and is_available()

            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = generate_cache_key(key_prefix, *args, **kwargs)

            if redis_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(f"CACHE HIT (Redis): {cache_key}")
                        return reconstruct_object(cached_value)
                except RedisOperationError:
                    _cache_stats["errors"] += 1
            else:
                cached_value = mem_cache_get(cache_key)
                if cached_value is not None:
                    _cache_stats["hits"] += 1
                    logger.debug(f"CACHE HIT (Memory): {cache_key}")
                    return reconstruct_object(cached_value)

            _cache_stats["misses"] += 1
            logger.debug(f"CACHE MISS: {cache_key}")

            async_func = cast(Callable[..., Awaitable[Any]], func)
            result = await async_func(*args, **kwargs)
            serializable_result = ensure_serializable(result)

            cache_ttl = ttl if ttl is not None else 300
            if redis_ready and client is not None:
                cache_ttl = ttl if ttl is not None else client.ttl_cache

            if redis_ready:
                assert client is not None
                try:
                    client.set_json(
                        cache_key,
                        cast(JsonSerializable, serializable_result),
                        ttl=cache_ttl,
                    )
                except RedisOperationError as e:
                    _cache_stats["errors"] += 1
                    logger.warning(f"Failed to cache result for {cache_key}: {e}")

            mem_cache_set(cache_key, serializable_result, cache_ttl)
            return result

        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            global _cache_stats

            if skip_cache_if and skip_cache_if(*args, **kwargs):
                return func(*args, **kwargs)

            client = get_client()
            redis_ready = client is not None and is_available()

            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = generate_cache_key(key_prefix, *args, **kwargs)

            if redis_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(f"CACHE HIT (Redis): {cache_key}")
                        return reconstruct_object(cached_value)
                except RedisOperationError:
                    _cache_stats["errors"] += 1
            else:
                cached_value = mem_cache_get(cache_key)
                if cached_value is not None:
                    _cache_stats["hits"] += 1
                    logger.debug(f"CACHE HIT (Memory): {cache_key}")
                    return reconstruct_object(cached_value)

            _cache_stats["misses"] += 1
            logger.debug(f"CACHE MISS: {cache_key}")

            result = func(*args, **kwargs)
            serializable_result = ensure_serializable(result)

            cache_ttl = ttl if ttl is not None else 300
            if redis_ready and client is not None:
                cache_ttl = ttl if ttl is not None else client.ttl_cache

            if redis_ready:
                assert client is not None
                try:
                    client.set_json(
                        cache_key,
                        cast(JsonSerializable, serializable_result),
                        ttl=cache_ttl,
                    )
                except RedisOperationError as e:
                    _cache_stats["errors"] += 1
                    logger.warning(f"Failed to cache result for {cache_key}: {e}")

            mem_cache_set(cache_key, serializable_result, cache_ttl)
            return result

        final_wrapper = async_wrapper if is_async else wrapper

        wrapper_any = cast(Any, final_wrapper)
        wrapper_any.cache_key_prefix = (
            prefix or f"cache:{func.__module__}.{func.__name__}"
        )
        wrapper_any.invalidate = lambda *args, **kwargs: invalidate_cached(
            key_builder(*args, **kwargs)
            if key_builder
            else generate_cache_key(wrapper_any.cache_key_prefix, *args, **kwargs)
        )

        return final_wrapper

    return decorator


def get_cache_stats() -> dict:
    """Get cache statistics dict."""
    return _cache_stats.copy()


def reset_cache_stats_internal() -> None:
    """Reset internal cache statistics."""
    global _cache_stats
    _cache_stats = {"hits": 0, "misses": 0, "errors": 0}


def invalidate_cached(key: str) -> bool:
    """Invalidate a cached value."""
    from .operations import cache_delete as _cache_delete

    return _cache_delete(key)
