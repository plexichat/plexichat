"""
Decorator-based caching with Valkey and in-memory fallback.

Provides the @cached decorator that supports both sync and async functions.
"""

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
from ..valkey_client import (
    get_client,
    is_available,
    ValkeyOperationError,
    JsonSerializable,
)
from .base import mem_cache_get, mem_cache_set, default_cache_ttl
from .serialization import generate_cache_key, ensure_serializable, reconstruct_object

_cache_stats: dict = {"hits": 0, "misses": 0, "errors": 0}

# First-parameter names that mark the decorated callable as a method. The
# bound instance is part of every call's ``args`` but must not participate in
# the default cache key: its ``repr`` carries the object's memory address, so
# including it pins each key to one instance (no cross-instance hits) and
# mints a fresh, never-reused key per instance (key explosion in Valkey).
_METHOD_SELF_NAMES = frozenset({"self", "cls", "mcs"})


def _drops_first_arg(func: Callable[..., Any]) -> bool:
    """Return True when the decorated callable is a method.

    Detected via the conventional first-parameter name (``self``/``cls``).
    Plain functions keep every argument in the key; only methods drop the
    bound instance, which carries no request identity (callers partition by
    arguments such as the authenticated user instead).
    """
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return False
    if not params:
        return False
    first = params[0]
    return first.name in _METHOD_SELF_NAMES and first.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def cached(
    ttl: Optional[int] = None,
    prefix: Optional[str] = None,
    key_builder: Optional[Callable[..., str]] = None,
    skip_cache_if: Optional[Callable[..., bool]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to cache function results in Valkey with in-memory fallback.

    Args:
        ttl: Time-to-live in seconds. Defaults to cache TTL from config.
        prefix: Custom key prefix. Defaults to function name.
        key_builder: Custom function to build cache key from arguments.
        skip_cache_if: Function that returns True to skip caching for this call.

    When applied to a method (first parameter named ``self``/``cls``), the
    bound instance is excluded from the default cache key so that cache
    entries are shared across instances instead of being pinned to one
    object's memory address. Custom ``key_builder`` callables always receive
    the full original arguments.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_async = inspect.iscoroutinefunction(func)
        # Bound-method detection happens once, at decoration time.
        _drop_first = _drops_first_arg(func)

        def _key_args(args: tuple) -> tuple:
            if _drop_first and args:
                return args[1:]
            return args

        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            global _cache_stats

            if skip_cache_if and skip_cache_if(*args, **kwargs):
                async_func = cast(Callable[..., Awaitable[Any]], func)
                return await async_func(*args, **kwargs)

            client = get_client()
            valkey_ready = client is not None and is_available()

            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = generate_cache_key(key_prefix, *_key_args(args), **kwargs)

            if valkey_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(f"CACHE HIT (Valkey): {cache_key}")
                        # Keep the local L1 warm so lookups still succeed from
                        # memory if Valkey goes down right after this hit.
                        mem_cache_set(
                            cache_key,
                            cached_value,
                            ttl if ttl is not None else default_cache_ttl(),
                        )
                        return reconstruct_object(cached_value)
                except ValkeyOperationError:
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

            cache_ttl = ttl if ttl is not None else default_cache_ttl()

            if valkey_ready:
                assert client is not None
                try:
                    client.set_json(
                        cache_key,
                        cast(JsonSerializable, serializable_result),
                        ttl=cache_ttl,
                    )
                except ValkeyOperationError as e:
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
            valkey_ready = client is not None and is_available()

            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = generate_cache_key(key_prefix, *_key_args(args), **kwargs)

            if valkey_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(f"CACHE HIT (Valkey): {cache_key}")
                        # Keep the local L1 warm so lookups still succeed from
                        # memory if Valkey goes down right after this hit.
                        mem_cache_set(
                            cache_key,
                            cached_value,
                            ttl if ttl is not None else default_cache_ttl(),
                        )
                        return reconstruct_object(cached_value)
                except ValkeyOperationError:
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

            cache_ttl = ttl if ttl is not None else default_cache_ttl()

            if valkey_ready:
                assert client is not None
                try:
                    client.set_json(
                        cache_key,
                        cast(JsonSerializable, serializable_result),
                        ttl=cache_ttl,
                    )
                except ValkeyOperationError as e:
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
            else generate_cache_key(
                wrapper_any.cache_key_prefix, *_key_args(args), **kwargs
            )
        )

        return final_wrapper

    return decorator


def get_cache_stats() -> dict:
    """Get cache statistics dict (mutable reference for internal use)."""
    return _cache_stats


def reset_cache_stats_internal() -> None:
    """Reset internal cache statistics."""
    global _cache_stats
    _cache_stats = {"hits": 0, "misses": 0, "errors": 0}


def invalidate_cached(key: str) -> bool:
    """Invalidate a cached value."""
    from .operations import cache_delete as _cache_delete

    return _cache_delete(key)
