"""Regression test for (M5): bounded LRU + TTL checksum caches.

Proves:
- inserting > maxsize keys keeps size <= maxsize (boundedness);
- oldest (least-recently-used) entries are evicted first;
- TTL expiry drops stale entries;
- basic thread-safety under concurrent writers.
"""

import threading
import time

from src.api.routes import avatars as avatar_routes


def test_checksum_caches_are_bounded():
    cls = avatar_routes._BoundedChecksumCache
    cache = cls(maxsize=100, ttl=600.0)
    for i in range(250):
        avatar_routes._cache_set_checksum(cache, i, f"c{i}")
    assert len(cache) <= 100


def test_oldest_evicted_lru_refresh():
    cls = avatar_routes._BoundedChecksumCache
    cache = cls(maxsize=5, ttl=600.0)
    for i in range(5):
        cache[i] = f"c{i}"
    # Touch key 0 so it becomes most-recently-used.
    assert cache.get(0) == "c0"
    # Insert 2 fresh keys -> evicts 1 and 2 (least-recently-used), not 0.
    cache[5] = "c5"
    cache[6] = "c6"
    assert len(cache) <= 5
    assert 0 in cache
    assert 1 not in cache
    assert 2 not in cache


def test_ttl_expiry():
    cls = avatar_routes._BoundedChecksumCache
    cache = cls(maxsize=100, ttl=0.05)
    cache[1] = "abc"
    assert cache.get(1) == "abc"
    time.sleep(0.08)
    assert cache.get(1) is None
    assert 1 not in cache


def test_thread_safety_smoke():
    cls = avatar_routes._BoundedChecksumCache
    cache = cls(maxsize=500, ttl=600.0)

    def writer(start):
        for i in range(start, start + 300):
            cache[i] = f"v{i}"
            cache.get(i)

    threads = [threading.Thread(target=writer, args=(t * 300,)) for t in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(cache) <= 500


def test_module_caches_are_bounded_m5():
    # The live module-level caches must be the bounded type, not plain dicts.
    assert isinstance(
        avatar_routes._avatar_checksum_cache, avatar_routes._BoundedChecksumCache
    )
    assert isinstance(
        avatar_routes._icon_checksum_cache, avatar_routes._BoundedChecksumCache
    )
    # Dict-style interface used by routes/invalidation helpers still works.
    avatar_routes._avatar_checksum_cache[4242] = "old"
    assert 4242 in avatar_routes._avatar_checksum_cache
    avatar_routes._invalidate_user_avatar_cache(4242)
    assert 4242 not in avatar_routes._avatar_checksum_cache
