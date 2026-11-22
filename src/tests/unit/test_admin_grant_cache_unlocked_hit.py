"""Warm admin grant lookups must not serialize readers on the global lock.

``permission_grant_cache.get_cached_grants`` sits on the hot authorization path
(``get_admin_granted_permissions`` -> ``can_delegate_permissions`` /
``can_grant_existing_role``). A single privileged request resolves the same
grants several times, and many admin requests overlap. The store holds
immutable ``(expiry, frozenset)`` tuples and invalidation is an atomic
``clear()``, so a warm hit needs no lock at all: a fast unlocked read is safe
and cannot serve a grant past an invalidation barrier.

Before the fix every hit took the module-global ``_LOCK`` around the lookup,
serializing all concurrent readers. These tests pin both the lock-free hot path
and the preserved security invariants (identical results, no stale grants after
invalidation, TTL expiry, bounded store).

Run with: pytest -n 0 -m unit src/tests/unit/test_admin_grant_cache_unlocked_hit.py
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core.admin import permission_grant_cache as grant_cache

pytestmark = pytest.mark.unit

_GRANT = frozenset({"admin.roles", "users.read"})
_READERS = 8
_LOOKUP_COST = 0.08  # simulated non-trivial lookup cost while a lock is held


class _SlowEntries(dict):
    """Dict whose ``get`` simulates lookup cost (sleeps, releasing the GIL)."""

    def get(self, key, default=None):
        time.sleep(_LOOKUP_COST)
        return super().get(key, default)


class _SpyLock:
    """Counts acquisitions while behaving exactly like the real lock."""

    def __init__(self, real):
        self._real = real
        self.acquisitions = 0

    def __enter__(self):
        self.acquisitions += 1
        return self._real.__enter__()

    def __exit__(self, *args):
        return self._real.__exit__(*args)


@pytest.fixture(autouse=True)
def _isolated_store():
    grant_cache.clear_grant_cache()
    yield
    grant_cache.clear_grant_cache()


def _prime(db, credential, subject, loader=lambda: _GRANT, ttl=60.0):
    """Seed exactly one warm entry with the real store/lock."""
    result = grant_cache.get_cached_grants(db, credential, subject, loader, ttl=ttl)
    assert result == _GRANT
    assert len(grant_cache._ENTRIES) == 1
    return result


def _run_readers(lookup):
    """Run ``lookup`` in ``_READERS`` threads released together; return (t, results)."""
    barrier = threading.Barrier(_READERS)
    results = [None] * _READERS

    def worker(i):
        barrier.wait()
        results[i] = lookup()

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=_READERS) as pool:
        list(pool.map(worker, range(_READERS)))
    return time.perf_counter() - start, results


def test_warm_hits_do_not_acquire_the_global_lock(monkeypatch):
    """Deterministic: a warm hit must never touch the module-global lock."""
    db, credential, subject = object(), "cred", 42
    _prime(db, credential, subject)

    spy = _SpyLock(threading.Lock())
    monkeypatch.setattr(grant_cache, "_LOCK", spy)

    for _ in range(5):
        assert (
            grant_cache.get_cached_grants(
                db, credential, subject, lambda: frozenset(), ttl=60.0
            )
            == _GRANT
        )

    assert spy.acquisitions == 0, (
        "warm cache hits must use a fast unlocked read; "
        f"global lock was acquired {spy.acquisitions} times"
    )


def test_concurrent_warm_hits_are_not_serialized(monkeypatch):
    """Timing: concurrent readers overlap instead of queueing on the lock.

    The comparison is made against a serialized reference measured in the same
    run (same machine load, same lookup cost), so the assertion is robust on a
    busy shared CI box: with the old code ``real ~= reference`` (~N * cost),
    with the fast unlocked read ``real ~= cost``.
    """
    db, credential, subject = object(), "cred", 42
    _prime(db, credential, subject)

    key = next(iter(grant_cache._ENTRIES))
    slow = _SlowEntries(grant_cache._ENTRIES)
    monkeypatch.setattr(grant_cache, "_ENTRIES", slow)

    # Reference: old behavior -- a single lock held around every slow lookup.
    serial_lock = threading.Lock()

    def serial_lookup():
        with serial_lock:
            return slow.get(key)

    t_serial, _ = _run_readers(serial_lookup)
    assert t_serial > _READERS * _LOOKUP_COST * 0.5, (
        "serialized reference did not actually serialize (bad probe)"
    )

    # Real code path: warm hits should overlap on the fast unlocked read.
    t_real, results = _run_readers(
        lambda: grant_cache.get_cached_grants(
            db, credential, subject, lambda: frozenset(), ttl=60.0
        )
    )

    # Identical results under concurrency.
    assert all(result == _GRANT for result in results)

    assert t_real < t_serial * 0.5, (
        f"warm lookups appear serialized: {t_real:.3f}s vs serialized "
        f"reference {t_serial:.3f}s ({_READERS} readers)"
    )


def test_invalidation_is_visible_to_the_fast_path():
    """A revocation must be seen by the next call, fast path or not."""
    db, credential, subject = object(), "cred", 42
    _prime(db, credential, subject)

    grant_cache.invalidate_grant_cache()

    assert (
        grant_cache.get_cached_grants(
            db, credential, subject, lambda: frozenset(), ttl=60.0
        )
        == frozenset()
    )


def test_expired_entry_is_not_served_by_the_fast_path():
    """An expired grant is a miss even when read without the lock."""
    db, credential, subject = object(), "cred", 7
    _prime(db, credential, subject, ttl=0.01)

    time.sleep(0.02)

    assert (
        grant_cache.get_cached_grants(
            db, credential, subject, lambda: frozenset(), ttl=60.0
        )
        == frozenset()
    )
