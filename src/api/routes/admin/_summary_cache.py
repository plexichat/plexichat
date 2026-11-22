"""Short-TTL cache for expensive read-only admin summaries.

The dashboard/report/alert read handlers gather their data with a dozen or so
COUNT/full-scan queries per request.  Operators poll those endpoints every few
seconds, so the same summary is recomputed over and over.  This module stores
the finished response for a short window and collapses those polls into a
single computation.

Concurrent identical polls *on a cold cache* are collapsed further by
per-key single-flight: the first caller computes while the rest wait on one
shared outcome instead of each running the whole dozen-query batch.  This is a
process-local tier, so a fleet of N workers computes at most N times per burst
(one leader per worker), not N x pollers.  In-flight bookkeeping is bounded so
a hostile stream of distinct keys cannot grow it without limit.

Two invariants make the cache safe:

* The key is scoped to the backing database (object identity + configured
  path), so a re-pointed test database or a freshly opened production file can
  never observe another database's cached summary.  The single-flight key is
  *that same key*, so two scopes can never share an in-flight computation or
  its result.
* Callers perform the host gate and the permission-scope gate **before**
  consulting the cache.  A cached value is therefore only ever returned to a
  caller who has already been authorized on this request, and the cached
  payloads are system-wide summaries that contain no per-admin data.
"""

from __future__ import annotations

import itertools
import threading
import time
import weakref
from typing import Any, Callable, Hashable

_LOCK = threading.Lock()
_ENTRIES: dict[tuple[Any, ...], tuple[float, Any]] = {}
_MAX_ENTRIES = 128

# Per-key single-flight state.  A leader publishes its outcome (value or
# exception) on ``_InFlight`` and wakes every waiter through the event; the
# entry is removed as soon as the outcome is published.  The registry is
# capped so a burst of distinct keys falls back to independent computes rather
# than growing waiter state without bound.
_MAX_INFLIGHT = 64


class _InFlight:
    """One shared outcome for a set of concurrent identical callers."""

    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


_INFLIGHT: dict[tuple[Any, ...], _InFlight] = {}

_STATS = {
    "hits": 0,
    "misses": 0,
    "computes": 0,
    "coalesced": 0,
    "inflight_rejected": 0,
    "inflight_peak": 0,
}

# A monotonic token per live DB object.  Using ``id(db)`` alone is unsafe in a
# test suite: once a fake DB is collected CPython may hand its id to a new DB,
# which would then read another database's cached summary.  A weak table keeps
# the token stable for the object's lifetime and releases it on collection.
_DB_TOKENS: "weakref.WeakKeyDictionary[Any, int]" = weakref.WeakKeyDictionary()
_DB_TOKEN_LOCK = threading.Lock()
_DB_TOKEN_COUNTER = itertools.count(1)

DEFAULT_TTL_SECONDS = 2.0


def _db_generation() -> Any:
    """Identify the current database without caching its rows themselves."""
    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            return None
        cfg = getattr(db, "config", None)
        path = cfg.get("path") if isinstance(cfg, dict) else None
        try:
            with _DB_TOKEN_LOCK:
                token = _DB_TOKENS.get(db)
                if token is None:
                    token = next(_DB_TOKEN_COUNTER)
                    _DB_TOKENS[db] = token
        except TypeError:
            # Non-weakrefable/unhashable DB double: fall back to identity+path.
            token = id(db)
        return (token, path)
    except Exception:
        return None


def _make_key(namespace: str, params: Hashable) -> tuple[Any, ...]:
    return (namespace, _db_generation(), params)


def get_or_compute(
    namespace: str,
    params: Hashable,
    producer: Callable[[], Any],
    ttl: float = DEFAULT_TTL_SECONDS,
) -> Any:
    """Return a cached summary, computing it at most once per live key.

    A cache hit is served immediately.  On a miss the first caller becomes the
    leader and runs ``producer`` outside the lock (so a slow query never blocks
    unrelated keys); concurrent callers for the *same key* wait on one shared
    outcome instead of each repeating the dozen-query batch.  Waiters receive
    the leader's exact value, or the exact same exception if it failed -- a
    failed compute is never cached, so the next call retries.

    The in-flight registry is bounded: past ``_MAX_INFLIGHT`` distinct keys a
    caller computes independently rather than adding unbounded waiter state.
    Callers still run the host/permission gates before reaching this function,
    and the key already carries the DB/authz scope, so coalescing can never
    cross a scope boundary.
    """
    key = _make_key(namespace, params)
    now = time.monotonic()
    leader = False
    flight: _InFlight | None = None

    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None and entry[0] > now:
            _STATS["hits"] += 1
            return entry[1]

        flight = _INFLIGHT.get(key)
        if flight is None:
            if len(_INFLIGHT) >= _MAX_INFLIGHT:
                # Bounded fallback: compute on our own rather than letting a
                # key flood grow the waiter registry without limit.
                _STATS["inflight_rejected"] += 1
                leader = True
                flight = None
            else:
                flight = _InFlight()
                _INFLIGHT[key] = flight
                leader = True
                _STATS["misses"] += 1
                _STATS["inflight_peak"] = max(_STATS["inflight_peak"], len(_INFLIGHT))
        else:
            _STATS["coalesced"] += 1

    if not leader:
        # Wait on the leader's outcome; it is always published (success or
        # error) because the leader sets the event in a finally-style path.
        assert flight is not None  # guaranteed: only non-leaders wait
        flight.event.wait()
        if flight.error is not None:
            raise flight.error
        return flight.value

    try:
        value = producer()
    except BaseException as exc:
        if flight is not None:
            with _LOCK:
                flight.error = exc
                if _INFLIGHT.get(key) is flight:
                    _INFLIGHT.pop(key, None)
            flight.event.set()
        raise

    with _LOCK:
        if flight is not None:
            flight.value = value
            if _INFLIGHT.get(key) is flight:
                _INFLIGHT.pop(key, None)
        if len(_ENTRIES) >= _MAX_ENTRIES:
            for stale in [k for k, (exp, _) in _ENTRIES.items() if exp <= now]:
                _ENTRIES.pop(stale, None)
            while len(_ENTRIES) >= _MAX_ENTRIES:
                oldest = min(_ENTRIES, key=lambda k: _ENTRIES[k][0])
                _ENTRIES.pop(oldest, None)
        _ENTRIES[key] = (now + ttl, value)
        _STATS["computes"] += 1
    if flight is not None:
        flight.event.set()
    return value


def stats() -> dict:
    """Snapshot of coalescing counters and current registry sizes."""
    with _LOCK:
        result = dict(_STATS)
        result["size"] = len(_ENTRIES)
        result["inflight"] = len(_INFLIGHT)
        return result


def reset_stats() -> None:
    """Zero the counters without disturbing cached entries."""
    with _LOCK:
        for key in _STATS:
            _STATS[key] = 0


def warm(
    namespace: str,
    params: Hashable,
    producer: Callable[[], Any],
    ttl: float = DEFAULT_TTL_SECONDS,
) -> None:
    """Prime one request-invariant summary off the request path (best-effort).

    The caller passes the exact ``(namespace, params, producer, ttl)`` tuple its
    route uses on a cache miss, so a warmed entry is indistinguishable from a
    value the route would have cached itself -- no auth/scope gate is moved or
    skipped and nothing is synthesised into the payload. Both snapshots are
    single-entry, version/DB-keyed caches, so a second warm inside the TTL is a
    no-op. A cold build that fails (e.g. the database is not ready yet) is
    logged and swallowed: the route still computes on demand exactly as before.
    """
    try:
        get_or_compute(namespace, params, producer, ttl)
    except Exception as e:
        try:
            import utils.logger as logger

            logger.debug(f"Summary cache warm-up skipped ({namespace}): {e}")
        except Exception:
            pass


def clear(namespace: str | None = None) -> None:
    """Drop cached entries, optionally only those in ``namespace``."""
    with _LOCK:
        if namespace is None:
            _ENTRIES.clear()
            return
        for key in [k for k in _ENTRIES if k[0] == namespace]:
            _ENTRIES.pop(key, None)
