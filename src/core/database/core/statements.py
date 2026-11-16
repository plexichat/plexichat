"""Bounded LRU cache for prepared (placeholder-converted) SQL statements.

``Database.execute()`` used to re-run the whole preparation pipeline -- SQLite
comment sanitising, placeholder conversion, PostgreSQL type rewrites and
percent escaping -- on every call, even for the same query text on every
request. That work is pure and depends only on the SQL string, the engine type
and whether parameters are bound, so its result can be reused.

This is deliberately a cache of *statement text*, not of rows and not of live
driver cursors. It is thread-safe, size-bounded, and instrumented with
hit/miss/eviction counters so a regression in reuse shows up in metrics rather
than as a mysterious latency change.
"""

import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

from src.core.security.hardening import bounded_int

#: Room for the whole hot query surface of a service with a comfortable margin.
DEFAULT_STATEMENT_CACHE_SIZE = 256
#: A hostile or mis-set config must not turn the cache into an unbounded map.
MAX_STATEMENT_CACHE_SIZE = 4096

_MISSING = object()


class PreparedStatementCache:
    """Thread-safe bounded LRU mapping a preparation key to converted SQL."""

    def __init__(self, maxsize: int = DEFAULT_STATEMENT_CACHE_SIZE):
        self._maxsize = bounded_int(
            maxsize,
            "statement_cache_size",
            minimum=1,
            maximum=MAX_STATEMENT_CACHE_SIZE,
        )
        self._cache: "OrderedDict[Any, Any]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get_or_prepare(self, key: Any, factory: Callable[[], Any]) -> Any:
        """Return the prepared statement for ``key``, computing it on a miss.

        ``factory`` runs outside the lock: it only reads immutable SQL text and
        calls pure dialect helpers, so holding the lock across it would
        serialise unrelated first-time preparations. A racing miss that
        computes the same value simply discards its duplicate.
        """
        with self._lock:
            value = self._cache.get(key, _MISSING)
            if value is not _MISSING:
                self._hits += 1
                self._cache.move_to_end(key)
                return value
            self._misses += 1

        prepared = factory()

        with self._lock:
            existing = self._cache.get(key, _MISSING)
            if existing is not _MISSING:
                return existing
            self._cache[key] = prepared
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
                self._evictions += 1
        return prepared

    def clear(self) -> None:
        """Drop all prepared statements (counters are cumulative by design)."""
        with self._lock:
            self._cache.clear()

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


def get_prepared_statement(
    cache: Optional[PreparedStatementCache], key: Any, factory: Callable[[], Any]
) -> Any:
    """Prepare through ``cache`` when present, otherwise compute directly.

    Keeps the execution mixin tolerant of partially constructed ``Database``
    instances (tests and legacy callers that never ran ``_init_once``).
    """
    if cache is None:
        return factory()
    return cache.get_or_prepare(key, factory)
