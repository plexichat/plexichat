"""
Decorator-based caching with Valkey and in-memory fallback.

Provides the @cached decorator that supports both sync and async functions.
The opt-in ``single_flight`` and ``stale_while_revalidate`` modes coalesce
concurrent cold misses and serve a bounded-stale local copy while one refresh
runs in the background. Both are off by default, so every other cached route
keeps the legacy Valkey-first behaviour.
"""

import asyncio
import inspect
import math
import threading
import time
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional,
    Tuple,
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
from .base import (
    mem_cache_clear_pattern,
    mem_cache_get,
    mem_cache_set,
    default_cache_ttl,
)
from .serialization import generate_cache_key, ensure_serializable, reconstruct_object

_cache_stats: dict = {"hits": 0, "misses": 0, "errors": 0}

# Hard absolute ceiling (seconds) for an opt-in process-local L1 entry. Even
# when a caller passes a large ``ttl``/``l1_ttl``, the staged copy dies within
# this window, bounding how stale another worker's invalidation can leave us.
# Only opt-in routes are affected; the default Valkey-first path is unchanged.
_L1_MAX_TTL_SECONDS = 30

# Hard ceiling (seconds) on how long a SWR hit may be served past the fresh L1
# deadline. This is the "bounded" part of stale-while-revalidate: even if a
# caller requests an enormous stale window, a value is never served older than
# ``_L1_MAX_TTL_SECONDS + _SWR_MAX_STALE_SECONDS`` after it was computed.
_SWR_MAX_STALE_SECONDS = 15
_SWR_DEFAULT_STALE_SECONDS = 5

# First-parameter names that mark the decorated callable as a method. The
# bound instance is part of every call's ``args`` but must not participate in
# the default cache key: its ``repr`` carries the object's memory address, so
# including it pins each key to one instance (no cross-instance hits) and
# mints a fresh, never-reused key per instance (key explosion in Valkey).
_METHOD_SELF_NAMES = frozenset({"self", "cls", "mcs"})

# Process-local single-flight / SWR state. Every map is keyed by the *exact*
# cache key, which already embeds the credential via the caller's key_builder,
# so two credentials can never share an in-flight result or a stale copy.
# All mutation happens under ``_flight_lock``; the write path takes it before
# ``base._mem_cache_lock`` (never the reverse), so the lock order is consistent
# with invalidation (``clear_local_and_bump``).
_flight_lock = threading.Lock()
_invalidation_generation = 0
_async_inflight: Dict[str, asyncio.Future] = {}
_sync_inflight: Dict[str, "_SyncFlight"] = {}
_swr_meta: Dict[str, Tuple[float, float]] = {}  # key -> (fresh_until, stale_until)
_background_tasks: set = set()


class _SyncFlight:
    """One in-flight sync computation shared by every waiter on a key."""

    __slots__ = ("done", "result", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None


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


def _track_task(task: asyncio.Future) -> None:
    """Keep a strong reference to a background task until it finishes."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def clear_flight_for_key(key: str) -> None:
    """Drop coalescing/stale state for one key and bump the generation.

    Any refresh captured before the bump will see a mismatched generation at
    write time and discard its result, so a mutation can never be undone by a
    background refresh that started before it.
    """
    global _invalidation_generation
    with _flight_lock:
        _invalidation_generation += 1
        _async_inflight.pop(key, None)
        _sync_inflight.pop(key, None)
        _swr_meta.pop(key, None)


def clear_local_and_bump(pattern: str) -> int:
    """Drop matching L1/SWR state and bump the generation atomically.

    Used by ``invalidate_pattern`` so a namespace invalidation (e.g. a reaction
    mutation) also clears the local stale copies and in-flight refreshes for
    that namespace, under the same lock the write path uses.
    """
    import fnmatch

    global _invalidation_generation
    with _flight_lock:
        count = mem_cache_clear_pattern(pattern)
        _invalidation_generation += 1
        for key in [k for k in _swr_meta if fnmatch.fnmatch(str(k), pattern)]:
            _swr_meta.pop(key, None)
            _async_inflight.pop(key, None)
            _sync_inflight.pop(key, None)
        return count


def reset_flight_state_internal() -> None:
    """Test helper: clear all process-local coalescing/stale state."""
    global _invalidation_generation
    with _flight_lock:
        _invalidation_generation += 1
        _async_inflight.clear()
        _sync_inflight.clear()
        _swr_meta.clear()


def pending_background_tasks() -> int:
    """Number of in-flight background refreshes (observability/tests)."""
    return len(_background_tasks)


def cached(
    ttl: Optional[int] = None,
    prefix: Optional[str] = None,
    key_builder: Optional[Callable[..., str]] = None,
    skip_cache_if: Optional[Callable[..., bool]] = None,
    *,
    l1_ttl: Optional[int] = None,
    single_flight: bool = False,
    stale_while_revalidate: bool = False,
    stale_ttl: Optional[int] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to cache function results in Valkey with in-memory fallback.

    Args:
        ttl: Time-to-live in seconds. Defaults to cache TTL from config.
        prefix: Custom key prefix. Defaults to function name.
        key_builder: Custom function to build cache key from arguments.
        skip_cache_if: Function that returns True to skip caching for this call.
        l1_ttl: When set, enable a bounded process-local (L1) read-through tier
            that is checked *before* Valkey. Warm requests avoid the Valkey
            round-trip entirely. The local entry lives at most ``l1_ttl``
            seconds, never longer than ``ttl``, and never longer than the hard
            ``_L1_MAX_TTL_SECONDS`` ceiling -- bounding stale reads after
            another worker invalidates/refreshes the shared entry even when a
            large TTL is configured. Callers MUST key the entry per credential
            (e.g. via ``authenticated_cache_key``) so one credential's private
            response is never served from another's L1 entry. Default ``None``
            keeps the legacy Valkey-first behaviour for every other cached
            route.
        single_flight: Opt-in. Coalesce concurrent misses for the same key into
            one computation; the other callers await the leader's result (or
            the same exception). Keyed by the credential-partitioned cache key,
            so no two credentials share a result. Default ``False``.
        stale_while_revalidate: Opt-in. Requires ``l1_ttl``. When a local entry
            is past its fresh deadline but still within a *bounded* stale
            window, serve it immediately and schedule a single background
            refresh. The window is ``stale_ttl`` (default 5s) capped by the hard
            ``_SWR_MAX_STALE_SECONDS`` ceiling. Invalidation drops the stale
            copy and any in-flight refresh. Default ``False``.
        stale_ttl: Stale window in seconds for ``stale_while_revalidate``;
            bounded by ``_SWR_MAX_STALE_SECONDS``. Ignored unless SWR is on.

    When applied to a method (first parameter named ``self``/``cls``), the
    bound instance is excluded from the default cache key so that cache
    entries are shared across instances instead of being pinned to one
    object's memory address. Custom ``key_builder`` callables always receive
    the full original arguments.
    """
    if stale_while_revalidate and l1_ttl is None:
        raise ValueError(
            "stale_while_revalidate requires l1_ttl: a local stale copy is "
            "needed to serve a value while the background refresh runs"
        )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_async = inspect.iscoroutinefunction(func)
        # Bound-method detection happens once, at decoration time.
        _drop_first = _drops_first_arg(func)

        def _key_args(args: tuple) -> tuple:
            if _drop_first and args:
                return args[1:]
            return args

        def _local_ttl() -> int:
            """Bounded L1 lifetime; never outlives the shared Valkey entry.

            For opt-in routes the lifetime is capped three ways: by ``l1_ttl``,
            by the shared ``ttl``, and by an absolute hard ceiling. The hard
            ceiling is what bounds cross-worker staleness when someone asks
            for a very long TTL. Default (non-opt-in) routes keep the full
            ``ttl`` so their fallback-tier behaviour is unchanged.
            """
            cache_ttl = ttl if ttl is not None else default_cache_ttl()
            if l1_ttl is None:
                return cache_ttl
            return min(cache_ttl, l1_ttl, _L1_MAX_TTL_SECONDS)

        def _swr_window() -> int:
            requested = (
                stale_ttl if stale_ttl is not None else _SWR_DEFAULT_STALE_SECONDS
            )
            return max(1, min(requested, _SWR_MAX_STALE_SECONDS))

        def _read_local(cache_key: str, check_local: bool) -> Tuple[str, Any]:
            """Return ``(kind, value)`` from the local tier.

            ``kind`` is one of ``fresh``, ``stale`` (SWR only), or ``miss``.
            ``check_local`` is False on the legacy Valkey-first path (no opt-in
            L1 while the backend is healthy), which must not read the fallback
            copy and serve a value a different worker has since refreshed.
            """
            if not check_local:
                return "miss", None
            with _flight_lock:
                meta = _swr_meta.get(cache_key) if stale_while_revalidate else None
            value = mem_cache_get(cache_key)
            if value is None:
                if meta is not None:
                    with _flight_lock:
                        _swr_meta.pop(cache_key, None)
                return "miss", None
            if meta is not None:
                now = time.time()
                if now < meta[0]:
                    return "fresh", reconstruct_object(value)
                if now < meta[1]:
                    return "stale", reconstruct_object(value)
                # Past the stale ceiling: drop the bookkeeping and treat as a
                # full miss (mem_cache_get has also expired the value itself).
                with _flight_lock:
                    _swr_meta.pop(cache_key, None)
                return "miss", None
            return "fresh", reconstruct_object(value)

        def _store(cache_key: str, serializable: Any, gen: int) -> bool:
            """Write through to Valkey and the local tier.

            Returns False (without writing) if an invalidation happened after
            ``gen`` was captured, so a refresh cannot resurrect stale data.
            """
            with _flight_lock:
                if gen != _invalidation_generation:
                    return False

            cache_ttl = ttl if ttl is not None else default_cache_ttl()
            # Resolve the backend here rather than capturing wrapper locals:
            # this helper also runs inside background refresh tasks.
            store_client = get_client()
            store_ready = store_client is not None and is_available()
            if store_ready:
                assert store_client is not None
                try:
                    store_client.set_json(
                        cache_key,
                        cast(JsonSerializable, serializable),
                        ttl=cache_ttl,
                    )
                except ValkeyOperationError as e:
                    _cache_stats["errors"] += 1
                    logger.warning(f"Failed to cache result for {cache_key}: {e}")

            now = time.time()
            local_ttl = _local_ttl()
            if stale_while_revalidate:
                fresh = now + local_ttl
                stale = fresh + _swr_window()
                local_expiry = max(1, int(math.ceil(stale - now)))
            else:
                fresh = stale = 0.0
                local_expiry = local_ttl

            with _flight_lock:
                if gen != _invalidation_generation:
                    return False
                mem_cache_set(cache_key, serializable, local_expiry)
                if stale_while_revalidate:
                    _swr_meta[cache_key] = (fresh, stale)
            return True

        # --- async paths ---------------------------------------------------

        async def _compute_async(
            cache_key: str,
            fut: asyncio.Future,
            gen: int,
            call_args: tuple,
            call_kwargs: dict,
        ) -> None:
            """Leader compute for a shared async future (cold or refresh)."""
            try:
                async_func = cast(Callable[..., Awaitable[Any]], func)
                result = await async_func(*call_args, **call_kwargs)
                serializable = ensure_serializable(result)
                _store(cache_key, serializable, gen)
                if not fut.done():
                    fut.set_result(result)
            except asyncio.CancelledError:
                # The leader was cancelled (e.g. loop shutdown): release every
                # waiter instead of leaving them blocked on a pending future.
                if not fut.done():
                    fut.cancel()
                raise
            except BaseException as exc:  # noqa: BLE001 - propagate to every waiter
                if not fut.done():
                    fut.set_exception(exc)
            finally:
                with _flight_lock:
                    if _async_inflight.get(cache_key) is fut:
                        _async_inflight.pop(cache_key, None)

        def _async_flight(cache_key: str, gen: int) -> Tuple[asyncio.Future, bool]:
            """Get (or create) the shared future for ``cache_key``."""
            with _flight_lock:
                fut = _async_inflight.get(cache_key)
                if fut is not None:
                    return fut, False
                fut = asyncio.get_running_loop().create_future()
                # Consume a possible exception so a cancelled leader does not
                # leave an "exception was never retrieved" warning behind.
                fut.add_done_callback(
                    lambda f: f.exception() if not f.cancelled() else None
                )
                _async_inflight[cache_key] = fut
                return fut, True

        def _schedule_async_refresh(
            cache_key: str, gen: int, call_args: tuple, call_kwargs: dict
        ) -> None:
            fut, leader = _async_flight(cache_key, gen)
            if leader:
                _track_task(
                    asyncio.get_running_loop().create_task(
                        _compute_async(cache_key, fut, gen, call_args, call_kwargs)
                    )
                )

        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            global _cache_stats

            if skip_cache_if and skip_cache_if(*args, **kwargs):
                async_func = cast(Callable[..., Awaitable[Any]], func)
                return await async_func(*args, **kwargs)

            client = get_client()
            valkey_ready = client is not None and is_available()
            gen = _invalidation_generation

            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = generate_cache_key(key_prefix, *_key_args(args), **kwargs)

            kind, value = _read_local(cache_key, l1_ttl is not None or not valkey_ready)
            if kind == "fresh":
                _cache_stats["hits"] += 1
                logger.debug(f"CACHE HIT (Memory L1): {cache_key}")
                return value
            if kind == "stale":
                _cache_stats["hits"] += 1
                logger.debug(f"CACHE HIT (Memory L1 stale): {cache_key}")
                _schedule_async_refresh(cache_key, gen, args, kwargs)
                return value

            if valkey_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(f"CACHE HIT (Valkey): {cache_key}")
                        # Keep the local L1 warm so lookups still succeed from
                        # memory if Valkey goes down right after this hit.
                        _store(cache_key, cached_value, gen)
                        return reconstruct_object(cached_value)
                except ValkeyOperationError:
                    _cache_stats["errors"] += 1

            _cache_stats["misses"] += 1
            logger.debug(f"CACHE MISS: {cache_key}")

            if single_flight:
                fut, leader = _async_flight(cache_key, gen)
                if leader:
                    _track_task(
                        asyncio.get_running_loop().create_task(
                            _compute_async(cache_key, fut, gen, args, kwargs)
                        )
                    )
                return await fut

            async_func = cast(Callable[..., Awaitable[Any]], func)
            result = await async_func(*args, **kwargs)
            serializable_result = ensure_serializable(result)
            _store(cache_key, serializable_result, gen)
            return result

        # --- sync paths ----------------------------------------------------

        def _compute_sync(
            cache_key: str,
            flight: _SyncFlight,
            gen: int,
            call_args: tuple,
            call_kwargs: dict,
        ) -> None:
            try:
                result = func(*call_args, **call_kwargs)
                serializable = ensure_serializable(result)
                _store(cache_key, serializable, gen)
                flight.result = result
            except BaseException as exc:  # noqa: BLE001 - propagate to waiters
                flight.error = exc
            finally:
                flight.done.set()
                with _flight_lock:
                    if _sync_inflight.get(cache_key) is flight:
                        _sync_inflight.pop(cache_key, None)

        def _sync_flight(cache_key: str) -> Tuple[_SyncFlight, bool]:
            with _flight_lock:
                flight = _sync_inflight.get(cache_key)
                if flight is not None:
                    return flight, False
                flight = _SyncFlight()
                _sync_inflight[cache_key] = flight
                return flight, True

        def _schedule_sync_refresh(
            cache_key: str, gen: int, call_args: tuple, call_kwargs: dict
        ) -> None:
            flight, leader = _sync_flight(cache_key)
            if leader:
                threading.Thread(
                    target=_compute_sync,
                    args=(cache_key, flight, gen, call_args, call_kwargs),
                    daemon=True,
                ).start()

        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            global _cache_stats

            if skip_cache_if and skip_cache_if(*args, **kwargs):
                return func(*args, **kwargs)

            client = get_client()
            valkey_ready = client is not None and is_available()
            gen = _invalidation_generation

            key_prefix = prefix or f"cache:{func.__module__}.{func.__name__}"
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                cache_key = generate_cache_key(key_prefix, *_key_args(args), **kwargs)

            kind, value = _read_local(cache_key, l1_ttl is not None or not valkey_ready)
            if kind == "fresh":
                _cache_stats["hits"] += 1
                logger.debug(f"CACHE HIT (Memory L1): {cache_key}")
                return value
            if kind == "stale":
                _cache_stats["hits"] += 1
                logger.debug(f"CACHE HIT (Memory L1 stale): {cache_key}")
                _schedule_sync_refresh(cache_key, gen, args, kwargs)
                return value

            if valkey_ready:
                assert client is not None
                try:
                    cached_value = client.get_json(cache_key)
                    if cached_value is not None:
                        _cache_stats["hits"] += 1
                        logger.debug(f"CACHE HIT (Valkey): {cache_key}")
                        _store(cache_key, cached_value, gen)
                        return reconstruct_object(cached_value)
                except ValkeyOperationError:
                    _cache_stats["errors"] += 1

            _cache_stats["misses"] += 1
            logger.debug(f"CACHE MISS: {cache_key}")

            if single_flight:
                flight, leader = _sync_flight(cache_key)
                if leader:
                    _compute_sync(cache_key, flight, gen, args, kwargs)
                flight.done.wait()
                if flight.error is not None:
                    raise flight.error
                return flight.result

            result = func(*args, **kwargs)
            serializable_result = ensure_serializable(result)
            _store(cache_key, serializable_result, gen)
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
