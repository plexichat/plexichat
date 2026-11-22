"""Bounded, short-TTL memoization of final search results.

Authenticated search endpoints receive bursts of *identical* requests: clients
retry the same query, re-fetch the same results page, and autocomplete-style
UIs re-issue the same needle. Every miss repeats the whole hot path — backend
FTS query, per-result ACL verification, enrichment and ranking. This module
memoizes the **final** result of that pipeline for a short window so identical
requests collapse into a single computation.

A result cache on an authenticated search path is a cross-user leak risk, so
three invariants are enforced here rather than left to each caller:

* **Authz scope is part of every key.** ``make_key`` takes an explicit
  ``authz_scope`` (the subject user id plus, for message search, the resolved
  set of readable conversations). Two different users — or one user whose
  permissions changed and therefore resolves a different readable set — can
  never share an entry.
* **Bounded and short-lived.** An LRU of at most ``max_entries`` with a TTL of
  ``ttl_seconds`` caps memory and bounds staleness after an index or permission
  change to the TTL window.
* **Copied in and out.** Values are deep-copied on ``put`` and ``get`` so a
  caller mutating a returned result object cannot corrupt the memo (or read a
  value another request is modifying).

The cache is process-local and per manager instance; it is not a shared tier.
"""

from __future__ import annotations

import copy
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Hashable, Iterable, Optional, Tuple

#: Defaults, overridable per manager via ``_config`` (see ``get_result_cache``).
DEFAULT_TTL_SECONDS = 2.0
DEFAULT_MAX_ENTRIES = 128
CONFIG_TTL_KEY = "result_cache_ttl_seconds"
CONFIG_MAX_ENTRIES_KEY = "result_cache_max_entries"

#: Returned by ``SearchResultCache.get`` when there is no live entry. A sentinel
#: (rather than ``None``) is required because an empty result list is a valid,
#: cacheable value.
CACHE_MISS = object()


@dataclass
class _Entry:
    stored_at: float
    value: Any


class SearchResultCache:
    """A tiny LRU with a per-entry TTL.

    Not thread-safe by design: managers are used from a single request/process
    context and the operations below are O(1) and non-blocking. Worst case under
    concurrent access is a redundant recomputation or an extra eviction, never
    a wrong-user result (the key always carries the authz scope).
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[Hashable, _Entry]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable) -> Any:
        """Return a deep copy of the cached value, or ``CACHE_MISS``."""
        if self.ttl_seconds <= 0:
            self.misses += 1
            return CACHE_MISS
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return CACHE_MISS
        if time.monotonic() - entry.stored_at >= self.ttl_seconds:
            self._entries.pop(key, None)
            self.misses += 1
            return CACHE_MISS
        self._entries.move_to_end(key)
        self.hits += 1
        return copy.deepcopy(entry.value)

    def put(self, key: Hashable, value: Any) -> None:
        """Store a deep copy of ``value``, evicting the least-recently-used."""
        if self.ttl_seconds <= 0:
            return
        self._entries[key] = _Entry(time.monotonic(), copy.deepcopy(value))
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._entries)


def make_key(version: str, authz_scope: Iterable[Hashable], **params: Any) -> Tuple:
    """Build a hashable cache key from an authz scope plus request params.

    The authz scope must come first so it is impossible to add a parameter
    without also carrying the scope. Sequences (readable-conversation sets,
    id filters) are normalized to sorted tuples so logically identical scopes
    hit the same entry regardless of set iteration order.
    """

    def normalize(value: Any) -> Any:
        if isinstance(value, (set, frozenset)):
            return tuple(sorted(value))
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    scope = tuple(normalize(part) for part in authz_scope)
    normalized_params = tuple(
        sorted((name, normalize(value)) for name, value in params.items())
    )
    return (version, scope, normalized_params)


def _caches(manager: Any) -> Dict[str, SearchResultCache]:
    """Return this manager's private cache registry (created on first use)."""
    return manager.__dict__.setdefault("_search_result_caches", {})


def get_result_cache(
    manager: Any,
    name: str,
    *,
    ttl_seconds: Optional[float] = None,
    max_entries: Optional[int] = None,
) -> SearchResultCache:
    """Get (or lazily create) a named cache attached to ``manager``.

    TTL/size come from an explicit override, then the manager's ``_config``,
    then module defaults. Reading config on each call keeps tests able to
    disable the cache (``ttl_seconds=0``) without reconstructing the manager.
    """
    config = getattr(manager, "_config", {}) or {}
    if ttl_seconds is None:
        ttl_seconds = config.get(CONFIG_TTL_KEY, DEFAULT_TTL_SECONDS)
    if max_entries is None:
        max_entries = config.get(CONFIG_MAX_ENTRIES_KEY, DEFAULT_MAX_ENTRIES)

    ttl = float(ttl_seconds) if ttl_seconds is not None else DEFAULT_TTL_SECONDS
    entries = int(max_entries) if max_entries is not None else DEFAULT_MAX_ENTRIES

    cache = _caches(manager).get(name)
    if cache is None:
        cache = _caches(manager)[name] = SearchResultCache(ttl, max(1, entries))
    else:
        cache.ttl_seconds = ttl
        cache.max_entries = max(1, entries)
    return cache


def clear_result_caches(manager: Any) -> None:
    """Drop every result cache attached to ``manager`` (invalidation/tests)."""
    for cache in _caches(manager).values():
        cache.clear()
