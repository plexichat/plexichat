"""Regressions for the bounded process-local (L1) cache tier.

The shared in-memory tier is both the offline fallback and the opt-in L1 stage
behind ``@cached(l1_ttl=...)``. These tests pin four invariants that a future
"performance" tweak must not silently drop:

* Bounds: the tier is capped by *entry count* AND by approximate *bytes*; a
  single oversized value cannot blow the byte ceiling or evict the whole tier.
* TTL: an opt-in L1 entry has a hard absolute lifetime ceiling (even when a
  huge ``ttl``/``l1_ttl`` is requested), while non-opt-in routes keep their
  full TTL (no regression to the default Valkey-first path).
* Concurrency: a warm L1 hit must not block on the writer/eviction lock, and
  concurrent sets/expiry-cleanups must keep the tier within its caps and the
  byte bookkeeping consistent.
* Correctness: invalidation clears both tiers, and per-credential keys never
  let one credential read another's private L1 entry.
"""

import asyncio
import itertools
import threading
import time

import pytest

from src.core.database.cache import base, decorators, operations
from src.core.database.cache.base import (
    _mem_cache,
    mem_cache_delete,
    mem_cache_get,
    mem_cache_set,
)
from src.core.database.cache.serialization import generate_cache_key
from src.core.database.cache.decorators import reset_cache_stats_internal


# ---------------------------------------------------------------------------
# fixtures / fakes
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_mem_tier():
    _mem_cache.clear()
    base._mem_cache_sizes.clear()
    base._mem_cache_total_bytes = 0
    reset_cache_stats_internal()
    yield
    _mem_cache.clear()
    base._mem_cache_sizes.clear()
    base._mem_cache_total_bytes = 0


@pytest.fixture
def offline(monkeypatch):
    """No Valkey: the in-memory tier is the only cache stage."""
    for module in (decorators, operations):
        monkeypatch.setattr(module, "get_client", lambda: None)
        monkeypatch.setattr(module, "is_available", lambda: False)


class FakeValkey:
    def __init__(self):
        self.store = {}
        self.gets = 0

    def get_json(self, key):
        self.gets += 1
        return self.store.get(key)

    def set_json(self, key, value, ttl=None):
        self.store[key] = value

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    def keys(self, pattern):
        import fnmatch

        return [k for k in self.store if fnmatch.fnmatch(k, pattern)]


@pytest.fixture
def backend(monkeypatch):
    client = FakeValkey()
    for module in (decorators, operations):
        monkeypatch.setattr(module, "get_client", lambda: client)
        monkeypatch.setattr(module, "is_available", lambda: True)
    return client


def _consistent_bytes() -> bool:
    return sum(base._mem_cache_sizes.values()) == base._mem_cache_total_bytes


# ---------------------------------------------------------------------------
# bounds: entry cap + byte cap
# ---------------------------------------------------------------------------


def test_entry_cap_is_respected_and_keeps_the_newest_keys(monkeypatch):
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_SIZE", 8)
    for i in range(40):
        mem_cache_set(f"bounded:{i}", i, 60)

    assert len(_mem_cache) == 8
    assert all(f"bounded:{i}" in _mem_cache for i in range(32, 40))
    assert "bounded:0" not in _mem_cache
    assert _consistent_bytes()


def test_byte_cap_is_respected(monkeypatch):
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_BYTES", 4096)
    for i in range(100):
        mem_cache_set(f"bytes:{i}", {"i": i, "blob": "x" * 128}, 60)

    assert len(_mem_cache) < 100  # eviction happened for bytes, not entries
    assert base._mem_cache_total_bytes <= 4096
    assert _consistent_bytes()


def test_oversized_single_value_is_not_cached_and_does_not_flush_tier(monkeypatch):
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_BYTES", 1024)
    mem_cache_set("keep:me", {"small": True}, 60)
    before = len(_mem_cache)

    mem_cache_set("huge", "x" * 100_000, 60)

    assert "huge" not in _mem_cache
    assert "keep:me" in _mem_cache
    assert len(_mem_cache) == before
    assert base._mem_cache_total_bytes <= 1024
    assert _consistent_bytes()


def test_replacing_and_deleting_a_key_do_not_drift_byte_accounting():
    mem_cache_set("k", "a" * 100, 60)
    first = base._mem_cache_total_bytes
    mem_cache_set("k", "a" * 1000, 60)
    second = base._mem_cache_total_bytes

    assert second < first * 12  # replaced, not accumulated
    assert _consistent_bytes()
    assert mem_cache_delete("k") is True
    assert base._mem_cache_total_bytes == 0
    assert _consistent_bytes()


def test_external_dict_clear_is_reconciled_by_next_set():
    mem_cache_set("a", "a" * 500, 60)
    _mem_cache.clear()  # some callers/tests do this directly

    mem_cache_set("b", "b" * 500, 60)

    assert list(_mem_cache) == ["b"]
    assert _consistent_bytes()


# ---------------------------------------------------------------------------
# TTL: hard ceiling for opt-in L1, no regression for default routes
# ---------------------------------------------------------------------------


def test_opt_in_l1_has_a_hard_ttl_ceiling(offline):
    @decorators.cached(ttl=3600, l1_ttl=3600, prefix="hard_ceiling_probe")
    def load(n):
        return n * 2

    assert load(7) == 14
    key = generate_cache_key(load.cache_key_prefix, 7)
    expiry, _ = _mem_cache[key]

    remaining = expiry - time.time()
    assert remaining > 0
    assert remaining <= decorators._L1_MAX_TTL_SECONDS + 1


def test_async_opt_in_l1_has_a_hard_ttl_ceiling(offline):
    @decorators.cached(ttl=7200, l1_ttl=7200, prefix="hard_ceiling_async_probe")
    async def load(n):
        return n * 3

    assert asyncio.run(load(5)) == 15
    key = generate_cache_key(load.cache_key_prefix, 5)
    expiry, _ = _mem_cache[key]
    assert 0 < expiry - time.time() <= decorators._L1_MAX_TTL_SECONDS + 1


def test_default_route_keeps_its_full_ttl(offline):
    """The hard ceiling is opt-in only; legacy routes must not regress."""

    @decorators.cached(ttl=3600, prefix="default_ttl_probe")
    def load(n):
        return n

    assert load(3) == 3
    key = generate_cache_key(load.cache_key_prefix, 3)
    expiry, _ = _mem_cache[key]
    assert expiry - time.time() > 1000


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


def test_warm_l1_hit_does_not_wait_on_the_writer_lock(offline):
    @decorators.cached(ttl=60, l1_ttl=60, prefix="lockfree_read_probe")
    def load(n):
        return n * 2

    load(1)  # warm the L1
    key = generate_cache_key(load.cache_key_prefix, 1)
    assert key in _mem_cache

    result = []

    def reader():
        result.append(mem_cache_get(key))

    with base._mem_cache_lock:
        worker = threading.Thread(target=reader)
        worker.start()
        worker.join(timeout=2.0)

    assert not worker.is_alive(), "warm L1 read blocked on the writer lock"
    assert result == [2]


def test_expired_entry_cleanup_is_race_free(offline):
    key = "expired_race_probe"
    mem_cache_set(key, {"x": 1}, 0)  # expires immediately

    errors = []

    def reader():
        try:
            for _ in range(100):
                mem_cache_get(key)
        except Exception as exc:  # noqa: BLE001 - surface any race in the assertion
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert key not in _mem_cache
    assert base._mem_cache_total_bytes == 0
    assert _consistent_bytes()


def test_concurrent_sets_and_gets_stay_within_caps(monkeypatch, offline):
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_SIZE", 32)
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_BYTES", 8192)
    errors = []

    def writer(offset):
        try:
            for i in range(200):
                key = f"stress:{offset}:{i}"
                mem_cache_set(key, {"i": i, "pad": "x" * 32}, 60)
                mem_cache_get(key)
                if i % 10 == 0:
                    mem_cache_delete(key)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert len(_mem_cache) <= 32
    assert base._mem_cache_total_bytes <= 8192
    assert _consistent_bytes()


# ---------------------------------------------------------------------------
# invalidation across tiers / credential isolation
# ---------------------------------------------------------------------------


def test_invalidation_clears_both_tiers(backend):
    calls = itertools.count()

    @decorators.cached(ttl=3600, l1_ttl=5, prefix="invalidate_both_probe")
    def load(user):
        return {"v": next(calls), "user": user}

    first = load(1)
    key = generate_cache_key(load.cache_key_prefix, 1)
    assert key in _mem_cache
    assert key in backend.store
    gets_after_cold = backend.gets

    # Warm hit is served from L1 without another Valkey GET.
    assert load(1) == first
    assert backend.gets == gets_after_cold

    # A write invalidates the shared key: both stages must be cleared.
    assert decorators.invalidate_cached(key) is True
    assert key not in _mem_cache
    assert key not in backend.store

    fresh = load(1)
    assert fresh != first  # recomputed, not served from a stale L1 copy
    assert backend.gets == gets_after_cold + 1  # a real GET on the miss
    assert key in _mem_cache


def test_pattern_invalidation_clears_l1_too(backend):
    mem_cache_set("pattern_probe:one", {"x": 1}, 60)
    mem_cache_set("pattern_probe:two", {"x": 2}, 60)
    backend.store["pattern_probe:one"] = {"x": 1}

    removed = operations.invalidate_pattern("pattern_probe:*")

    assert removed >= 2
    assert "pattern_probe:one" not in _mem_cache
    assert "pattern_probe:two" not in _mem_cache
    assert "pattern_probe:one" not in backend.store
    assert base._mem_cache_total_bytes == 0


def test_l1_entries_are_partitioned_per_credential(offline):
    def key_builder(credential, **kwargs):
        return f"per_credential:{credential}"

    @decorators.cached(ttl=60, l1_ttl=5, key_builder=key_builder)
    def private(credential):
        return {"secret": f"data-of-{credential}"}

    alice = private("alice")
    bob = private("bob")
    alice_again = private("alice")

    assert alice == alice_again == {"secret": "data-of-alice"}
    assert bob == {"secret": "data-of-bob"}
    assert "per_credential:alice" in _mem_cache
    assert "per_credential:bob" in _mem_cache
    assert _mem_cache["per_credential:alice"][1] != _mem_cache["per_credential:bob"][1]
