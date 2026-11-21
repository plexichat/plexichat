"""PERF regression: ``BoundedLRU.set`` never full-scans the cache on a write.

Two rounds of hardening are captured here:

* expired keys are only reclaimed under capacity pressure (spare capacity means
  a stale-but-unread key is harmless, because ``get`` drops it on access); and
* reclamation uses a bounded expiry min-heap instead of walking ``_data`` and
  allocating a list of every entry. At capacity a bounded cache breaches the
  entry cap on *every* insert, so the old ``list(self._data.items())`` sweep was
  O(n) work plus a full snapshot allocation per write.

TTL/fail-closed behaviour is unchanged: ``get`` still refuses and drops expired
entries, and expired keys are still reclaimed before live LRU entries once
there is capacity pressure. Pure unit test: no app, config, DB or network.
"""

from collections import OrderedDict

import pytest

import src.core.bounded_lru as blru
from src.core.bounded_lru import BoundedLRU

pytestmark = [pytest.mark.unit]


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


class _CountingOrderedDict(OrderedDict):
    """Internal store that records full-snapshot scans performed by ``set``."""

    def __init__(self) -> None:
        super().__init__()
        self.items_calls = 0

    def items(self):
        self.items_calls += 1
        return super().items()


def test_set_does_not_scan_whole_cache_below_capacity(monkeypatch):
    monkeypatch.setattr(blru, "time", _Clock())
    cache = BoundedLRU(max_entries=1000, max_bytes=10**6, ttl_seconds=60)
    store = _CountingOrderedDict()
    cache._data = store

    for i in range(50):
        cache.set(i, i)

    # Before the fix this was 50 (one full scan + list allocation per write).
    assert store.items_calls == 0
    assert len(cache) == 50


def test_set_does_not_full_scan_under_pressure(monkeypatch):
    """At capacity every write breaches the cap; none may walk all entries."""
    monkeypatch.setattr(blru, "time", _Clock())
    cache = BoundedLRU(max_entries=2, max_bytes=10**6, ttl_seconds=60)
    store = _CountingOrderedDict()
    cache._data = store

    cache.set("a", "a")
    cache.set("b", "b")
    before = store.items_calls
    cache.set("c", "c")  # 3 entries > 2 -> pressure, but no full snapshot scan

    assert store.items_calls == before
    assert len(cache) == 2  # live LRU entry was still evicted
    assert cache.get("b") == "b" and cache.get("c") == "c"


def test_expired_keys_reclaimed_before_live_lru_under_pressure(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(blru, "time", clock)
    cache = BoundedLRU(max_entries=3, max_bytes=10**6, ttl_seconds=5)

    cache.set("stale", "x")
    clock.now += 6  # "stale" is expired now
    cache.set("a", "a")
    cache.set("b", "b")
    cache.set("c", "c")  # over capacity -> sweep drops "stale" first

    assert "stale" not in cache
    assert cache.get("a") == "a"
    assert cache.get("b") == "b"
    assert cache.get("c") == "c"
    assert cache.stats()["evictions"] == 0  # no live key was evicted


def test_expired_value_is_never_served_after_conditional_sweep(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(blru, "time", clock)
    cache = BoundedLRU(max_entries=8, max_bytes=10**6, ttl_seconds=5)
    cache.set("k", "verdict")

    clock.now += 6
    assert cache.get("k", "MISS") == "MISS"  # fail closed, no stale verdict
    assert "k" not in cache
    assert cache.stats()["expirations"] >= 1


def test_at_capacity_writes_do_not_full_scan(monkeypatch):
    """Regression: 2x-capacity writes used to cause one full scan per write.

    With ``max_entries=1000`` and 2000 distinct writes the primitive is at its
    cap for the second half; the old implementation walked ~1000 entries on
    each of those writes (~1.5M entries scanned). The heap index does zero
    ``items()`` snapshots.
    """
    monkeypatch.setattr(blru, "time", _Clock())
    cache = BoundedLRU(max_entries=1000, max_bytes=10**9, ttl_seconds=60)
    store = _CountingOrderedDict()
    cache._data = store

    for i in range(2000):
        cache.set(i, "v")

    assert store.items_calls == 0
    assert len(cache) == 1000


def test_expired_entries_reclaimed_via_heap_without_full_scan(monkeypatch):
    """Expired keys are reclaimed through the heap, not a full snapshot scan."""
    clock = _Clock()
    monkeypatch.setattr(blru, "time", clock)
    cache = BoundedLRU(max_entries=64, max_bytes=10**6, ttl_seconds=5)
    store = _CountingOrderedDict()
    cache._data = store

    for i in range(64):
        cache.set(i, "v")
    clock.now += 6  # all 64 entries are now expired
    before = cache.stats()["expirations"]
    cache.set("fresh", "v")  # 65 > 64 -> pressure -> reclaim via heap

    assert store.items_calls == 0
    assert cache.stats()["expirations"] - before == 64
    assert len(cache) == 1
    assert cache.get("fresh") == "v"


def test_expiry_heap_stays_bounded_under_hot_resets(monkeypatch):
    """Stale heap entries from re-sets are compacted, so memory stays bounded."""
    monkeypatch.setattr(blru, "time", _Clock())
    cache = BoundedLRU(max_entries=4, max_bytes=10**6, ttl_seconds=60)

    for i in range(500):
        cache.set("hot", i)

    assert len(cache) == 1
    assert len(cache._expiry_heap) <= 2 * cache.max_entries
