"""Focused tests for the bounded LRU cache and its instrumentation.

Before: the shared cache was bounded but opaque -- there was no way to tell
whether it was helping or thrashing, and no test pinned the eviction policy.
After: hit/miss/eviction counters are maintained under the cache lock and
exposed via stats(), and these tests pin the bound, LRU ordering and counters.
"""

import threading

import pytest

from src.api.lru_cache import LRUCache


def test_hits_and_misses_are_counted():
    cache = LRUCache(maxsize=4)
    cache["a"] = 1

    assert cache.get("a") == 1
    assert cache.get("missing") is None
    assert cache.get("missing", "fallback") == "fallback"

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["size"] == 1
    assert stats["maxsize"] == 4


def test_bound_holds_and_evictions_are_counted():
    cache = LRUCache(maxsize=3)
    for i in range(50):
        cache[i] = i

    assert len(cache) == 3
    assert cache.stats()["evictions"] == 47
    assert cache.stats()["size"] == 3


def test_least_recently_used_entry_is_evicted():
    cache = LRUCache(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2

    # Reading "a" refreshes its recency, so "b" becomes the eviction victim.
    assert cache.get("a") == 1
    cache["c"] = 3

    assert "a" in cache
    assert "b" not in cache
    assert "c" in cache


def test_updating_existing_key_does_not_evict():
    cache = LRUCache(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2
    cache["a"] = 99  # update, not a new entry

    assert len(cache) == 2
    assert cache.stats()["evictions"] == 0
    assert cache.get("a") == 99


def test_stats_reset_keeps_entries():
    cache = LRUCache(maxsize=2)
    cache["a"] = 1
    cache.get("a")
    cache.get("missing")

    cache.reset_stats()

    assert cache.stats()["hits"] == 0
    assert cache.stats()["misses"] == 0
    assert cache.get("a") == 1
    assert cache.stats()["hits"] == 1


def test_clear_drops_entries_but_not_counters():
    cache = LRUCache(maxsize=2)
    cache["a"] = 1
    cache.get("a")

    cache.clear()

    assert len(cache) == 0
    assert cache.stats()["hits"] == 1


def test_invalid_maxsize_is_rejected():
    with pytest.raises(ValueError):
        LRUCache(maxsize=0)
    with pytest.raises(ValueError):
        LRUCache(maxsize=-1)


def test_counters_are_consistent_under_concurrency():
    cache = LRUCache(maxsize=8)
    threads = []

    def worker(offset):
        for i in range(200):
            key = (offset + i) % 16
            cache[key] = i
            cache.get(key)

    for offset in range(4):
        thread = threading.Thread(target=worker, args=(offset,))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    stats = cache.stats()
    assert stats["size"] <= stats["maxsize"]
    assert stats["hits"] + stats["misses"] == 4 * 200
