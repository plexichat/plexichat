"""Bounded in-process cache for fully-validated proxied content.

The media proxy already persists fetched images in a database-backed cache,
but serving even a *cached* image still re-ran the SSRF validator's
``socket.getaddrinfo`` call. For a public hostname that DNS lookup alone costs
tens of milliseconds, which dominated the ``GET /api/v1/media/proxy`` latency.

This module adds a small, bounded, TTL'd LRU in front of that path. It stores
only the final ``(content_bytes, content_type)`` pair for a URL that has
*already* passed the proxy's full SSRF/content/security checks. A cache hit
therefore performs no DNS resolution and no outbound fetch, while a miss goes
through the unchanged validation path.

Security properties (see ``security/proxy.py`` for the integration):

* The cheap, network-free structural SSRF gate runs on **every** request,
  including cache hits, so a forbidden URL can never be served from cache.
* An entry is only inserted after full URL validation, content-type
  allow-listing, SVG/magic-byte sniffing and the body-size cap have passed.
* Errors and oversized bodies are never inserted.
* Both an entry-count bound and a total-byte bound are enforced; the
  least-recently-used entry is evicted first. Entries expire after a TTL, and
  upstream ``Cache-Control`` directives can shorten or forbid caching.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Hashable, Optional, Tuple


class ProxyContentCache:
    """Thread-safe, bounded, TTL'd LRU keyed by the validated source URL.

    Values are arbitrary payloads; callers supply ``size`` (in bytes) so the
    byte budget can be enforced independently of the object shape.
    """

    def __init__(
        self,
        maxsize: int = 256,
        max_bytes: int = 32 * 1024 * 1024,
        ttl_seconds: int = 86400,
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        self._max_bytes = max(1, int(max_bytes))
        self._ttl = max(0, int(ttl_seconds))
        # key -> (expires_at_monotonic, payload, size)
        self._entries: "OrderedDict[Hashable, Tuple[float, Any, int]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "expired": 0,
            "evictions": 0,
            "not_cached": 0,
        }

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def ttl(self) -> int:
        return self._ttl

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return a live payload or ``default``; expired entries are dropped."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return default
            expires, payload, size = entry
            if expires <= now:
                self._entries.pop(key, None)
                self._bytes -= size
                self._stats["expired"] += 1
                self._stats["misses"] += 1
                return default
            self._entries.move_to_end(key)
            self._stats["hits"] += 1
            return payload

    def put(
        self,
        key: Hashable,
        payload: Any,
        size: int = 1,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Insert ``payload`` unless it alone exceeds the byte budget."""
        size = max(1, int(size))
        if size > self._max_bytes:
            with self._lock:
                self._stats["not_cached"] += 1
            return

        ttl = self._ttl if ttl_seconds is None else max(0, int(ttl_seconds))
        expires = time.monotonic() + ttl
        with self._lock:
            if key in self._entries:
                self._bytes -= self._entries[key][2]
            self._entries[key] = (expires, payload, size)
            self._bytes += size
            self._entries.move_to_end(key)
            self._evict_locked()

    def _evict_locked(self) -> None:
        while self._entries and (
            len(self._entries) > self._maxsize or self._bytes > self._max_bytes
        ):
            _key, (_expires, _payload, size) = self._entries.popitem(last=False)
            self._bytes -= size
            self._stats["evictions"] += 1

    def pop(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return default
            self._bytes -= entry[2]
            return entry[1]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict:
        with self._lock:
            result = dict(self._stats)
            result["size"] = len(self._entries)
            result["bytes"] = self._bytes
            return result

    def reset_stats(self) -> None:
        with self._lock:
            for key in self._stats:
                self._stats[key] = 0


__all__ = ["ProxyContentCache"]
