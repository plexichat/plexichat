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
_LOOKUP_COST = 0.03  # simulated non-trivial lookup cost while the lock is held


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
    """Timing: N concurrent readers overlap instead of queueing on the lock."""
    db, credential, subject = object(), "cred", 42
    _prime(db, credential, subject)

    # Equivalent store whose lookup sleeps: while ``_LOCK`` is held across the
    # lookup this serializes readers to ~N * cost; with a fast unlocked read the
    # sleeps overlap and total time approaches a single lookup cost.
    slow = _SlowEntries(grant_cache._ENTRIES)
    monkeypatch.setattr(grant_cache, "_ENTRIES", slow)

    barrier = threading.Barrier(_READERS)
    results = [None] * _READERS

    def worker(i):
        barrier.wait()
        results[i] = grant_cache.get_cached_grants(
            db, credential, subject, lambda: frozenset(), ttl=60.0
        )

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=_READERS) as pool:
        list(pool.map(worker, range(_READERS)))
    elapsed = time.perf_counter() - start

    # Identical results under concurrency.
    assert all(result == _GRANT for result in results)

    serialized_floor = _READERS * _LOOKUP_COST * 0.65
    assert elapsed < serialized_floor, (
        f"warm lookups appear serialized: {elapsed:.3f}s for {_READERS} readers "
        f"(expected < {serialized_floor:.3f}s if the hit path is unlocked)"
    )


def test_invalidation_is_visible_to_the_fast_path():
    """A revocation must be seen by the next call, fast path or not."""
    db, credential, subject = object(), "cred", 42
    _prime(db, credential, subject)

    grant_cache.invalidate_grant_cache()

    assert grant_cache.get_cached_grants(
        db, credential, subject, lambda: frozenset(), ttl=60.0
    ) == frozenset()


def test_expired_entry_is_not_served_by_the_fast_path():
    """An expired grant is a miss even when read without the lock."""
    db, credential, subject = object(), "cred", 7
    _prime(db, credential, subject, ttl=0.01)

    time.sleep(0.02)

    assert grant_cache.get_cached_grants(
        db, credential, subject, lambda: frozenset(), ttl=60.0
    ) == frozenset()
