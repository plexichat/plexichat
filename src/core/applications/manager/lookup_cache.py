"""Bounded, short-TTL cache for hot application metadata lookups.

``ApplicationManager.get_application`` is on several per-request read paths
(OAuth exchange, interaction handling, installation checks, bot profile
reads). The very same application row is frequently resolved more than once
while serving one request, and the global DB query cache is flushed by any
unrelated write, so those repeats keep reaching SQLite/Postgres.

This module provides a small process-local cache with two independent bounds
(``max_entries`` and ``max_bytes``) plus a short TTL. It is deliberately
generic; the application-specific sizing/copy helpers below keep secrets out
of the cache and hand callers an independent copy so cached metadata can never
be mutated through a returned object.

Entries are invalidated explicitly by the write paths in ``application_crud``;
the TTL only bounds staleness for out-of-band mutations performed through a
raw DB handle.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import fields, replace
from typing import Any, Callable, Dict, Hashable, Optional, Tuple

from ..models import Application

# Public miss sentinel so callers can distinguish a cached ``None`` from a
# miss without relying on a private object identity.
CACHE_MISS = object()


def application_byte_size(app: Application) -> int:
    """Approximate the retained size of an ``Application`` in bytes.

    Secret fields are skipped: the metadata cache never stores credentials,
    and excluding them keeps a large secret from consuming the byte budget.
    """
    total = 0
    for field in fields(app):
        if field.name in {"client_secret", "client_secret_hash"}:
            continue
        value = getattr(app, field.name, None)
        if value is None:
            continue
        if isinstance(value, str):
            total += len(value.encode("utf-8"))
        elif isinstance(value, (list, tuple)):
            for item in value:
                total += len(str(item).encode("utf-8"))
        else:
            total += len(str(value).encode("utf-8"))
    return total


def copy_application(app: Application) -> Application:
    """Return an independent copy so callers cannot mutate cached metadata."""
    return replace(
        app,
        redirect_uris=list(app.redirect_uris),
        allowed_scopes=list(app.allowed_scopes),
    )


class BoundedTTLCache:
    """Thread-safe LRU with an entry cap, a byte cap, and a short TTL.

    The oldest (least-recently-used) entries are evicted first until both
    bounds are respected. A single value larger than ``max_bytes`` is never
    cached. ``get`` returns :data:`CACHE_MISS` on a miss or an expired entry.
    """

    def __init__(
        self,
        *,
        max_entries: int = 128,
        max_bytes: int = 256 * 1024,
        ttl_seconds: float = 2.0,
        sizer: Optional[Callable[[Any], int]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_bytes = max(1, int(max_bytes))
        self._ttl = max(0.0, float(ttl_seconds))
        self._sizer = sizer or (lambda value: max(1, len(repr(value))))
        self._clock = clock or time.monotonic
        self._data: "OrderedDict[Hashable, Tuple[float, Any, int]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._stats: Dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "puts": 0,
            "evictions": 0,
            "invalidations": 0,
        }

    def stats(self) -> Dict[str, int]:
        """Return a snapshot of cache counters (observability/tests)."""
        with self._lock:
            return dict(self._stats)

    def clear(self) -> None:
        """Drop every cached entry (counters are preserved)."""
        with self._lock:
            self._data.clear()
            self._bytes = 0

    def get(self, key: Hashable) -> Any:
        """Return the cached value, or :data:`CACHE_MISS` on miss/expiry."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return CACHE_MISS
            expires_at, value, size = entry
            if self._clock() >= expires_at:
                self._data.pop(key, None)
                self._bytes -= size
                self._stats["misses"] += 1
                return CACHE_MISS
            self._data.move_to_end(key)
            self._stats["hits"] += 1
            return value

    def put(self, key: Hashable, value: Any) -> bool:
        """Cache ``value``; return ``False`` if it alone exceeds the byte cap."""
        size = max(1, int(self._sizer(value)))
        if size > self._max_bytes:
            return False
        with self._lock:
            existing = self._data.pop(key, None)
            if existing is not None:
                self._bytes -= existing[2]
            self._data[key] = (self._clock() + self._ttl, value, size)
            self._bytes += size
            self._data.move_to_end(key)
            self._evict_locked()
            self._stats["puts"] += 1
            return True

    def invalidate(self, key: Optional[Hashable] = None) -> None:
        """Drop one entry, or every entry when ``key`` is omitted."""
        with self._lock:
            if key is None:
                self._data.clear()
                self._bytes = 0
                return
            entry = self._data.pop(key, None)
            if entry is not None:
                self._bytes -= entry[2]
                self._stats["invalidations"] += 1

    def _evict_locked(self) -> None:
        while self._data and (
            len(self._data) > self._max_entries or self._bytes > self._max_bytes
        ):
            _, (_, _, size) = self._data.popitem(last=False)
            self._bytes -= size
            self._stats["evictions"] += 1


__all__ = [
    "CACHE_MISS",
    "BoundedTTLCache",
    "application_byte_size",
    "copy_application",
]
