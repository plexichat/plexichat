"""
Base types and in-memory cache fallback for the cache module.

Provides CacheError exception and in-memory cache dictionary
used as fallback when Valkey is unavailable.
"""

import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import utils.config as config

# Cache storage. Entries stay ``(expiry, value)`` 2-tuples: several callers and
# tests read that shape directly. Approximate per-key sizes live in a parallel
# dict so the tier can be bounded by bytes as well as by entry count.
_mem_cache: Dict[str, Tuple[float, Any]] = {}
_mem_cache_sizes: Dict[str, int] = {}
_mem_cache_total_bytes = 0
_MEM_CACHE_MAX_SIZE = 1000

# Soft byte ceiling for the process-local tier. A *single* value bigger than
# this is never cached (rather than evicting the whole tier for one entry), so
# one oversized response cannot blow the in-process memory bound.
_MEM_CACHE_MAX_BYTES = 8 * 1024 * 1024

# Guards every read-modify-write on _mem_cache. The cached decorator and
# cache invalidation run on unrelated threads (request handlers, SFU/event
# callbacks, background reapers); without this lock two threads racing in
# mem_cache_clear_pattern / the eviction loop snapshot the same key list
# and the second ``del`` raises KeyError (seen as
# ``KeyError: 'has_perm:...'`` under parallel test load).
_mem_cache_lock = threading.Lock()

# Default TTLs used by the in-memory fallback tier. Mirror valkey's
# ``ttl.*`` so both tiers behave identically.
_DEFAULT_TTLS = {"cache": 60, "presence": 300, "session": 1800}
_DEFAULT_CACHE_TTL = _DEFAULT_TTLS["cache"]


def default_ttl(kind: str = "cache") -> int:
    """Effective default TTL for ``kind`` (cache/presence/session), honoring
    the configured ``valkey.ttl.<kind>`` even when Valkey is not connected.
    Keeps the in-memory fallback and the Valkey tier on the same expiry."""
    try:
        valkey_cfg = config.get("valkey") or config.get("redis") or {}
        ttl = (valkey_cfg.get("ttl", {}) or {}).get(kind, _DEFAULT_TTLS.get(kind, 60))
        return int(ttl)
    except (TypeError, ValueError):
        return _DEFAULT_TTLS.get(kind, 60)


def default_cache_ttl() -> int:
    """Effective default cache TTL (see ``default_ttl``)."""
    return default_ttl("cache")


class CacheError(Exception):
    """Base exception for cache operations."""

    pass


def _approx_cache_size(value: Any) -> int:
    """Cheap, shallow estimate of a cached value's footprint in bytes.

    This is a bound, not an exact measurement: one container level is walked
    so a large list/dict of strings is sized meaningfully, while deeply nested
    or unknown objects fall back to their own ``getsizeof``. The estimate is
    deliberately bounded so the hot write path cannot itself be DoS'd by a
    deeply nested payload.
    """
    try:
        if isinstance(value, (str, bytes, bytearray)):
            return len(value) + 64
        if isinstance(value, dict):
            total = sys.getsizeof(value) + 64
            for key, item in value.items():
                total += (len(key) if isinstance(key, str) else 32) + 64
                total += sys.getsizeof(item) + 32
            return total
        if isinstance(value, (list, tuple, set, frozenset)):
            total = sys.getsizeof(value) + 64
            for item in value:
                total += sys.getsizeof(item) + 32
            return total
        return sys.getsizeof(value) + 64
    except Exception:
        # Fail closed with a conservative estimate rather than letting a
        # hostile __sizeof__/iteration skip the byte cap.
        return 1024


def _reconcile_sizes_locked() -> None:
    """Rebuild size bookkeeping if code outside this module mutated the dict.

    Tests (and a few callers) clear ``_mem_cache`` directly. Rebuilding on a
    key-set mismatch keeps the byte counter correct without taxing the common
    path, where the key sets stay in sync.
    """
    global _mem_cache_total_bytes
    if _mem_cache_sizes.keys() == _mem_cache.keys():
        return
    _mem_cache_sizes.clear()
    _mem_cache_total_bytes = 0
    for key, (_, value) in _mem_cache.items():
        size = _approx_cache_size(value)
        _mem_cache_sizes[key] = size
        _mem_cache_total_bytes += size


def _evict_to_fit_locked(extra_bytes: int) -> None:
    """Evict oldest (insertion-order) entries until both caps allow ``extra_bytes``.

    Re-inserting a key moves it to the end, so this behaves as a coarse LRU:
    a hot key that is refreshed often is protected from eviction.
    """
    global _mem_cache_total_bytes
    while _mem_cache and (
        len(_mem_cache) >= _MEM_CACHE_MAX_SIZE
        or _mem_cache_total_bytes + extra_bytes > _MEM_CACHE_MAX_BYTES
    ):
        oldest = next(iter(_mem_cache))
        _mem_cache.pop(oldest, None)
        _mem_cache_total_bytes -= _mem_cache_sizes.pop(oldest, 0)
    if _mem_cache_total_bytes < 0:  # defensive: external dict mutation
        _mem_cache_total_bytes = 0


def mem_cache_get(key: str) -> Optional[Any]:
    """Get a value from the in-memory cache, handling expiry.

    The hot hit path is lock-free: dict lookups are atomic under the CPython
    GIL, so a warm L1 read (the whole point of the opt-in decorator tier) does
    not serialise behind writers/evictions. Only an expired entry takes the
    lock, so it can be removed and its byte accounting updated safely.
    """
    global _mem_cache_total_bytes

    entry = _mem_cache.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if expiry > time.time():
        return value

    with _mem_cache_lock:
        # Re-check under the lock: another thread may have refreshed or
        # removed the entry while we waited, so never double-decrement.
        entry = _mem_cache.get(key)
        if entry is not None and entry[0] <= time.time():
            _mem_cache.pop(key, None)
            _mem_cache_total_bytes -= _mem_cache_sizes.pop(key, 0)
            if _mem_cache_total_bytes < 0:
                _mem_cache_total_bytes = 0
    return None


def mem_cache_set(key: str, value: Any, ttl: int) -> None:
    """Set a value in the in-memory cache with TTL, within the entry/byte caps."""
    global _mem_cache_total_bytes

    size = _approx_cache_size(value)
    with _mem_cache_lock:
        _reconcile_sizes_locked()

        # A single oversized value must not evict the entire tier or exceed
        # the byte ceiling on its own. Drop any prior copy of the key first.
        old = _mem_cache.pop(key, None)
        if old is not None:
            _mem_cache_total_bytes -= _mem_cache_sizes.pop(key, 0)
        if size > _MEM_CACHE_MAX_BYTES:
            if _mem_cache_total_bytes < 0:
                _mem_cache_total_bytes = 0
            return

        _evict_to_fit_locked(size)
        _mem_cache[key] = (time.time() + ttl, value)
        _mem_cache_sizes[key] = size
        _mem_cache_total_bytes += size


def mem_cache_delete(key: str) -> bool:
    """Delete a key from the in-memory cache."""
    global _mem_cache_total_bytes
    with _mem_cache_lock:
        if key in _mem_cache:
            _mem_cache.pop(key, None)
            _mem_cache_total_bytes -= _mem_cache_sizes.pop(key, 0)
            if _mem_cache_total_bytes < 0:
                _mem_cache_total_bytes = 0
            return True
        return False


def mem_cache_clear_pattern(pattern: str) -> int:
    """Clear in-memory cache entries matching a pattern."""
    global _mem_cache_total_bytes
    import fnmatch

    with _mem_cache_lock:
        keys = [k for k in _mem_cache.keys() if fnmatch.fnmatch(str(k), pattern)]
        for k in keys:
            _mem_cache.pop(k, None)
            _mem_cache_total_bytes -= _mem_cache_sizes.pop(k, 0)
        if _mem_cache_total_bytes < 0:
            _mem_cache_total_bytes = 0
        return len(keys)


def mem_cache_matching_keys(pattern: str) -> List[str]:
    """Return in-memory cache keys matching a glob ``pattern``."""
    import fnmatch

    with _mem_cache_lock:
        return [k for k in list(_mem_cache.keys()) if fnmatch.fnmatch(str(k), pattern)]
