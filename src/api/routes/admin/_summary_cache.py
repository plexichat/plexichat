"""Short-TTL cache for expensive read-only admin summaries.

The dashboard/report/alert read handlers gather their data with a dozen or so
COUNT/full-scan queries per request.  Operators poll those endpoints every few
seconds, so the same summary is recomputed over and over.  This module stores
the finished response for a short window and collapses those polls into a
single computation.

Two invariants make the cache safe:

* The key is scoped to the backing database (object identity + configured
  path), so a re-pointed test database or a freshly opened production file can
  never observe another database's cached summary.
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
    """Return a cached summary or compute (and cache) it once.

    ``producer`` is called outside the lock so a slow DB query never blocks
    unrelated cache lookups.  A concurrent duplicate computation is harmless:
    the last writer's value wins and both callers observe an equivalent,
    freshly computed summary.
    """
    key = _make_key(namespace, params)
    now = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]

    value = producer()

    with _LOCK:
        if len(_ENTRIES) >= _MAX_ENTRIES:
            for stale in [k for k, (exp, _) in _ENTRIES.items() if exp <= now]:
                _ENTRIES.pop(stale, None)
            while len(_ENTRIES) >= _MAX_ENTRIES:
                oldest = min(_ENTRIES, key=lambda k: _ENTRIES[k][0])
                _ENTRIES.pop(oldest, None)
        _ENTRIES[key] = (now + ttl, value)
    return value


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
