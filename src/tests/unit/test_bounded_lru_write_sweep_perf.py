"""PERF regression: ``BoundedLRU.set`` sweeps expired keys only under pressure.

Before the fix every ``set`` walked the whole cache (and built a list of all
entries) to drop expired keys -- O(n) work plus an allocation on every write,
even with vast spare capacity. The sweep is now gated on a bound being breached.

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


def test_set_still_scans_once_when_a_bound_is_breached(monkeypatch):
    monkeypatch.setattr(blru, "time", _Clock())
    cache = BoundedLRU(max_entries=2, max_bytes=10**6, ttl_seconds=60)
    store = _CountingOrderedDict()
    cache._data = store

    cache.set("a", "a")
    cache.set("b", "b")
    before = store.items_calls
    cache.set("c", "c")  # 3 entries > 2 -> one reclaim scan

    assert store.items_calls == before + 1
    assert len(cache) == 2


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
