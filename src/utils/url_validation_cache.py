"""Bounded, short-TTL memo for SSRF-validated URL resolutions.

``URLValidator.validate_url_for_request`` resolves every URL with
``socket.getaddrinfo`` on each call. The same endpoint is frequently validated
many times in a row -- a webhook delivery re-checks its endpoint immediately
before every POST and again on each redirect hop, embed/attachment validation
re-checks URLs that were already cleared on an earlier request, and the media
proxy re-validates cached content. For a public hostname that DNS lookup is the
dominant cost of the whole gate.

This module adds a small, bounded, TTL'd LRU in front of that one *expensive*
step. It stores only the ``(hostname, resolved_ip)`` pair for a URL that already
passed the **full** SSRF gate (scheme allow-list, authority-backslash rejection,
blocked-host / ``.local`` / ``.internal`` rejection, and rejection of every
resolved IP that is private/loopback/link-local/CGNAT/reserved). A hit therefore
skips DNS resolution but the cheap structural checks still run on every call.

Security properties (see ``security.URLValidator`` for the integration):

* The cheap, network-free structural SSRF gate runs on **every** request,
  including cache hits, so a forbidden URL can never be served from cache.
* An entry is inserted only after a successful validation whose resolved IP is
  a public address. Failures (bad scheme, backslash, blocked host, private/mixed
  DNS answers, NXDOMAIN, empty answers) are **never** cached, so a host that
  flips to an internal address is re-resolved and rejected rather than served
  from a stale entry.
* The key is the **full URL** plus the validator's blocked-host set, so two URLs
  that differ in path/query/scheme or in policy never share an entry.
* The TTL is deliberately short and the LRU is bounded, so a rebind to a
  private address is picked up on the next validation once the entry expires.
* Callers still connect to the returned ``resolved_ip`` (the resolve-once,
  pin-the-IP policy that defeats DNS rebinding); this cache never hands back a
  hostname for a second resolution.

The loopback allowance used by ``PLEXICHAT_SELF_TEST`` is intentionally not
cached: it is a test-only, env-gated shortcut with no DNS lookup to save.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Short by design: this is a burst-coalescing memo, not a durable DNS cache.
DEFAULT_TTL_SECONDS = _positive_float_env("PLEXICHAT_URL_VALIDATION_CACHE_TTL", 5.0)
DEFAULT_MAXSIZE = _positive_int_env("PLEXICHAT_URL_VALIDATION_CACHE_MAXSIZE", 512)

# Cache value: (hostname, resolved_ip) exactly as returned by the validator.
Resolution = Tuple[str, str]
# Key: (frozenset of blocked hosts, full URL string).
CacheKey = Tuple[frozenset, str]


class URLResolutionCache:
    """Thread-safe, bounded, TTL'd LRU of fully-validated URL resolutions.

    ``maxsize`` caps the entry count (LRU eviction). ``ttl_seconds`` caps how
    long an entry may be reused before DNS is resolved again. All timings use
    ``time.monotonic`` so wall-clock adjustments cannot extend an entry's life.
    """

    def __init__(
        self, maxsize: int = DEFAULT_MAXSIZE, ttl_seconds: float = DEFAULT_TTL_SECONDS
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        self._ttl = float(ttl_seconds)
        # key -> (expires_at_monotonic, resolution)
        self._entries: "OrderedDict[CacheKey, Tuple[float, Resolution]]" = OrderedDict()
        self._lock = threading.Lock()
        self._stats: Dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "inserts": 0,
            "evictions": 0,
            "expired": 0,
        }

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def ttl(self) -> float:
        return self._ttl

    def get(self, key: CacheKey) -> Optional[Resolution]:
        """Return a live resolution for ``key`` or ``None`` on miss/expiry."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return None
            expires, resolution = entry
            if expires <= now:
                self._entries.pop(key, None)
                self._stats["expired"] += 1
                self._stats["misses"] += 1
                return None
            self._entries.move_to_end(key)
            self._stats["hits"] += 1
            return resolution

    def put(self, key: CacheKey, resolution: Resolution) -> None:
        """Insert a validated resolution, evicting the LRU entry when full."""
        expires = time.monotonic() + self._ttl
        with self._lock:
            self._entries[key] = (expires, resolution)
            self._entries.move_to_end(key)
            self._stats["inserts"] += 1
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
                self._stats["evictions"] += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        with self._lock:
            result = dict(self._stats)
            result["size"] = len(self._entries)
            return result

    def reset_stats(self) -> None:
        with self._lock:
            for key in self._stats:
                self._stats[key] = 0


# Single process-wide memo: most call sites construct ``URLValidator()`` inline,
# so an instance-level cache would never see a hit. The blocked-host set is part
# of the key, so validators with custom policy cannot consume each other's
# entries.
_resolution_cache = URLResolutionCache()


def get_url_validation_cache() -> URLResolutionCache:
    """Return the process-wide URL resolution memo (for stats/tests)."""
    return _resolution_cache


def url_validation_cache_stats() -> dict:
    return _resolution_cache.stats()


def clear_url_validation_cache() -> None:
    """Drop every memoized resolution (config reload / test isolation)."""
    _resolution_cache.clear()


def reset_url_validation_cache_stats() -> None:
    _resolution_cache.reset_stats()


__all__ = [
    "URLResolutionCache",
    "Resolution",
    "CacheKey",
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_MAXSIZE",
    "get_url_validation_cache",
    "url_validation_cache_stats",
    "clear_url_validation_cache",
    "reset_url_validation_cache_stats",
]
