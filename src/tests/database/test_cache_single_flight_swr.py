"""Regressions for opt-in single-flight and bounded stale-while-revalidate.

These two modes are layered on the ``@cached`` decorator's L1 tier. They are
off by default (every other cached route keeps the legacy Valkey-first path)
and must preserve the three invariants of that tier:

* Bounded staleness: a SWR value is never older than the hard L1 ceiling plus
  the hard SWR ceiling, even with huge configured TTLs.
* Invalidation: dropping a key/namespace clears the stale copy *and* any
  in-flight refresh; a refresh that started before the invalidation cannot
  write its now-stale result back afterwards.
* Credential isolation: in-flight and stale state is keyed by the same
  credential-partitioned cache key as the stored value, so two credentials
  never share a computation or a copied response.
"""

import asyncio
import threading
import time

import pytest

from src.core.database.cache import base, decorators, operations
from src.core.database.cache.base import _mem_cache
from src.core.database.cache.decorators import (
    _L1_MAX_TTL_SECONDS,
    _SWR_MAX_STALE_SECONDS,
    pending_background_tasks,
    reset_cache_stats_internal,
    reset_flight_state_internal,
)
from src.core.database.cache.serialization import generate_cache_key


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate():
    def _clear():
        _mem_cache.clear()
        base._mem_cache_sizes.clear()
        base._mem_cache_total_bytes = 0
        reset_cache_stats_internal()
        reset_flight_state_internal()

    _clear()
    yield
    _clear()


@pytest.fixture
def offline(monkeypatch):
    """No Valkey: the process-local tier is the only cache stage."""
    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)


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


async def _drain_background_tasks():
    """Let scheduled async refreshes finish before asserting on the tier."""
    for _ in range(5):
        tasks = list(decorators._background_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)


def _force_stale(key, value, *, fresh_age=1, stale_for=10):
    """Move a warmed L1 entry into its SWR window without sleeping."""
    now = time.time()
    # Keep the value readable by base.mem_cache_get while marking it stale.
    _mem_cache[key] = (now + 60, value)
    decorators._swr_meta[key] = (now - fresh_age, now + stale_for)


# ---------------------------------------------------------------------------
# default off / configuration
# ---------------------------------------------------------------------------


def test_single_flight_is_opt_in(offline):
    """Without the flag, concurrent misses still each run the loader."""
    calls = []
    gate = threading.Event()

    @decorators.cached(ttl=60, l1_ttl=60, prefix="sf_off_probe")
    def load():
        calls.append(1)
        gate.wait(timeout=3)  # hold every miss open until all callers arrived
        return len(calls)

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        load()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    deadline = time.time() + 3
    while len(calls) < 8 and time.time() < deadline:
        time.sleep(0.001)
    gate.set()
    for t in threads:
        t.join()

    assert len(calls) == 8


def test_swr_requires_l1_ttl():
    with pytest.raises(ValueError, match="l1_ttl"):
        decorators.cached(stale_while_revalidate=True)


def test_non_opt_in_route_writes_no_swr_metadata(offline):
    @decorators.cached(ttl=60, prefix="no_swr_meta_probe")
    def load():
        return 1

    load()
    key = generate_cache_key(load.cache_key_prefix)
    assert decorators._swr_meta == {}
    assert key in _mem_cache  # fallback copy still warmed, as before


# ---------------------------------------------------------------------------
# single-flight: concurrent misses collapse to one computation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_single_flight_coalesces_concurrent_misses(offline):
    calls = []
    gate = asyncio.Event()

    @decorators.cached(ttl=60, l1_ttl=60, prefix="sf_async_probe", single_flight=True)
    async def load(key):
        calls.append(key)
        await gate.wait()
        return {"v": len(calls), "key": key}

    tasks = [asyncio.create_task(load("cred:alice")) for _ in range(24)]
    await asyncio.sleep(0)  # let the leader register the in-flight future
    gate.set()
    results = await asyncio.gather(*tasks)

    assert calls == ["cred:alice"]
    assert results == [{"v": 1, "key": "cred:alice"}] * 24


def test_sync_single_flight_coalesces_concurrent_misses(offline):
    """The measurable before/after: one loader call (and ~1x latency)."""
    calls = []
    loader_lock = threading.Lock()
    n_callers = 16

    @decorators.cached(ttl=60, l1_ttl=60, prefix="sf_sync_probe", single_flight=True)
    def load(key):
        calls.append(key)
        with loader_lock:  # serialized cold loader -> N*delay without SF
            time.sleep(0.02)
        return len(calls)

    barrier = threading.Barrier(n_callers)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait()
            results.append(load("cred:alice"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_callers)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start

    assert errors == []
    assert calls == ["cred:alice"]
    assert results == [1] * n_callers
    # Without coalescing this would be ~n_callers * 0.02s.
    assert elapsed < n_callers * 0.02 * 0.5


def test_single_flight_shares_exceptions(offline):
    calls = []
    started = threading.Event()
    release = threading.Event()

    @decorators.cached(ttl=60, l1_ttl=60, prefix="sf_error_probe", single_flight=True)
    def load():
        calls.append(1)
        started.set()
        release.wait(timeout=3)
        raise ValueError("cold loader failed")

    barrier = threading.Barrier(6)
    seen = []

    def worker():
        barrier.wait()
        try:
            load()
        except ValueError as exc:
            seen.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    assert started.wait(timeout=3)
    time.sleep(0.02)  # let every waiter join the in-flight before it fails
    release.set()
    for t in threads:
        t.join()

    assert len(calls) == 1  # loader ran once
    assert len(seen) == 6  # every waiter got the same exception
    assert len({id(e) for e in seen}) == 1


def test_single_flight_is_keyed_per_credential(offline):
    def key_builder(credential):
        return f"sf_cred:{credential}"

    calls = {"alice": 0, "bob": 0}

    @decorators.cached(
        ttl=60,
        l1_ttl=60,
        key_builder=key_builder,
        single_flight=True,
    )
    def load(credential):
        calls[credential] += 1
        time.sleep(0.01)
        return {"secret": credential}

    barrier = threading.Barrier(2)
    results = {}

    def worker(credential):
        barrier.wait()
        results[credential] = load(credential)

    threads = [threading.Thread(target=worker, args=(c,)) for c in ("alice", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls == {"alice": 1, "bob": 1}
    assert results == {
        "alice": {"secret": "alice"},
        "bob": {"secret": "bob"},
    }


# ---------------------------------------------------------------------------
# stale-while-revalidate: bounded staleness, one background refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swr_serves_stale_immediately_then_refreshes_once(offline):
    calls = []

    @decorators.cached(
        ttl=60,
        l1_ttl=2,
        stale_ttl=3,
        prefix="swr_hit_probe",
        single_flight=True,
        stale_while_revalidate=True,
    )
    async def load():
        calls.append(1)
        return {"v": len(calls)}

    first = await load()
    assert first == {"v": 1}
    key = generate_cache_key(load.cache_key_prefix)

    _force_stale(key, first)
    stale = await load()
    # The stale body is returned without waiting for the refresh.
    assert stale == first
    await _drain_background_tasks()
    assert len(calls) == 2  # exactly one refresh, not one per stale reader

    fresh = await load()
    assert fresh == {"v": 2}


@pytest.mark.asyncio
async def test_swr_window_and_lifetime_are_hard_bounded(offline):
    @decorators.cached(
        ttl=3600,
        l1_ttl=3600,
        stale_ttl=10_000,
        prefix="swr_bound_probe",
        stale_while_revalidate=True,
    )
    async def load():
        return "v"

    await load()
    key = generate_cache_key(load.cache_key_prefix)
    expiry, _ = _mem_cache[key]
    fresh_until, stale_until = decorators._swr_meta[key]

    # L1 ceiling preserved; stale window capped by its own hard ceiling.
    assert stale_until <= expiry + 1  # L1 entry outlives the fresh deadline
    assert stale_until - fresh_until <= _SWR_MAX_STALE_SECONDS + 1
    assert expiry - time.time() <= (_L1_MAX_TTL_SECONDS + _SWR_MAX_STALE_SECONDS + 2)


@pytest.mark.asyncio
async def test_swr_beyond_stale_window_is_a_full_miss(offline):
    calls = []

    @decorators.cached(
        ttl=60,
        l1_ttl=2,
        stale_ttl=3,
        prefix="swr_expired_probe",
        single_flight=True,
        stale_while_revalidate=True,
    )
    async def load():
        calls.append(1)
        return {"v": len(calls)}

    first = await load()
    key = generate_cache_key(load.cache_key_prefix)
    now = time.time()
    _mem_cache[key] = (now + 60, first)
    decorators._swr_meta[key] = (now - 100, now - 1)  # past the stale ceiling

    result = await load()
    assert result != first
    assert result == {"v": 2}
    assert len(calls) == 2


def test_swr_entries_are_partitioned_per_credential(offline):
    def key_builder(credential):
        return f"swr_cred:{credential}"

    @decorators.cached(
        ttl=60,
        l1_ttl=2,
        stale_ttl=3,
        key_builder=key_builder,
        single_flight=True,
        stale_while_revalidate=True,
    )
    def private(credential):
        return {"secret": f"data-of-{credential}"}

    alice = private("alice")
    bob = private("bob")

    assert alice == {"secret": "data-of-alice"}
    assert bob == {"secret": "data-of-bob"}
    assert _mem_cache["swr_cred:alice"][1] != _mem_cache["swr_cred:bob"][1]
    assert "swr_cred:alice" in decorators._swr_meta
    assert "swr_cred:bob" in decorators._swr_meta


# ---------------------------------------------------------------------------
# invalidation: no stale resurrection
# ---------------------------------------------------------------------------


def test_pattern_invalidation_clears_stale_copy_and_blocks_refresh(backend):
    calls = []
    gate = threading.Event()

    @decorators.cached(
        ttl=60,
        l1_ttl=2,
        stale_ttl=3,
        prefix="swr_inv_probe",
        single_flight=True,
        stale_while_revalidate=True,
    )
    def load(msg_id):
        n = len(calls) + 1
        calls.append(n)
        if n > 1:
            gate.wait(timeout=3)  # the background refresh blocks here
        return {"v": n}

    first = load(1)
    key = generate_cache_key(load.cache_key_prefix, 1)
    assert first == {"v": 1}
    assert key in backend.store

    _force_stale(key, first)
    stale = load(1)  # serve stale, schedule a refresh (call #2, now blocked)
    assert stale == first

    # A mutation invalidates the namespace while the refresh is in flight.
    operations.invalidate_pattern("swr_inv_probe:*")
    assert key not in _mem_cache
    assert key not in decorators._swr_meta
    assert key not in backend.store

    gate.set()  # let the stale refresh finish -- it must NOT write back
    time.sleep(0.05)
    assert key not in _mem_cache, "invalidation was undone by a stale refresh"
    assert key not in backend.store

    fresh = load(1)
    assert fresh != first
    assert fresh == {"v": 3}


def test_key_invalidation_clears_swr_state(backend):
    @decorators.cached(
        ttl=60,
        l1_ttl=2,
        stale_ttl=3,
        prefix="swr_key_inv_probe",
        single_flight=True,
        stale_while_revalidate=True,
    )
    def load():
        return {"v": 1}

    load()
    key = generate_cache_key(load.cache_key_prefix)
    assert key in decorators._swr_meta

    decorators.invalidate_cached(key)
    assert key not in _mem_cache
    assert key not in decorators._swr_meta
    assert key not in backend.store


@pytest.mark.asyncio
async def test_no_background_tasks_leak_after_refresh(offline):
    @decorators.cached(
        ttl=60,
        l1_ttl=2,
        stale_ttl=3,
        prefix="swr_tasks_probe",
        single_flight=True,
        stale_while_revalidate=True,
    )
    async def load():
        return 1

    first = await load()
    key = generate_cache_key(load.cache_key_prefix)
    _force_stale(key, first)
    await load()
    await _drain_background_tasks()
    assert pending_background_tasks() == 0
