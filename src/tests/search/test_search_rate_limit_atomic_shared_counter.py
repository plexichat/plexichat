"""Regression tests for the search rate limiter's shared-counter accounting.

The limiter used to maintain its per-user counter with a generic-cache
read-modify-write (``cache_get`` -> ``+1`` -> ``cache_set``).  Two workers that
read the same pre-write value both write ``value + 1``, so a burst of N
concurrent searches consumes only ONE unit of the budget.  When no shared
cache was configured it degraded to per-instance dicts, so N workers each got
a full independent budget.  Both mistakes multiply the caller's search budget
by the number of workers.

The fix routes the limit through the hardened shared rate-limit storage, whose
counter update is a single atomic INCR+EXPIRE operation.
"""

import time

import pytest

from src.core.search.exceptions import SearchRateLimitError
from src.core.search.manager import rate_limiting as rl
from src.core.database.cache import rate_limit as rl_storage


class _Limiter(rl.RateLimitMixin):
    """Minimal host for RateLimitMixin with a fixed configured limit."""

    def __init__(self, limit: int) -> None:
        self._config = {"rate_limit_per_minute": limit}
        # Attributes the old per-process fallback expected on the manager.
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}


def _install_shared_store(monkeypatch):
    """Back the hardened shared limiter with an in-memory atomic INCR store."""
    store = {}

    class FakeClient:
        def eval_lua(self, script, keys, args):
            key = keys[0]
            store[key] = store.get(key, 0) + 1
            return store[key]

        def incr(self, key):
            store[key] = store.get(key, 0) + 1
            return store[key]

        def expire(self, key, ttl):
            return True

        def get(self, key):
            return store.get(key)

        def set(self, key, value, ex=None):
            store[key] = value
            return True

    monkeypatch.setattr(rl_storage, "get_client", lambda: FakeClient(), raising=False)
    monkeypatch.setattr(rl_storage, "is_available", lambda: True, raising=False)
    return store


def test_concurrent_stale_reads_cannot_bypass_limit(monkeypatch):
    """N callers reading the same stale count must not all pass the gate.

    The vulnerable implementation re-read the generic cache counter for every
    request; modelling that with a stale snapshot makes every one of them see
    ``count == 0`` and compute ``1``, so none is ever refused.  An atomic
    shared counter increments once per call and therefore enforces the cap.
    """
    limit = 2
    _install_shared_store(monkeypatch)
    now_ms = int(time.time() * 1000)

    def stale_get(key):
        if key.endswith(":count"):
            return 0  # every concurrent caller observes the same pre-write value
        return now_ms  # live, non-expired window_start

    monkeypatch.setattr(rl, "valkey_available", lambda: True, raising=False)
    monkeypatch.setattr(rl, "cache_get", stale_get, raising=False)
    monkeypatch.setattr(rl, "cache_set", lambda *a, **k: True, raising=False)

    limiter = _Limiter(limit)
    for _ in range(limit):
        limiter._check_rate_limit(4242)
    with pytest.raises(SearchRateLimitError):
        limiter._check_rate_limit(4242)


def test_budget_is_shared_across_independent_instances(monkeypatch):
    """Two 'workers' must draw from one budget, not one budget each."""
    limit = 2
    _install_shared_store(monkeypatch)
    # Force the old code path (no generic cache) so an unfixed limiter falls
    # back to the per-instance dicts; the fixed path ignores this flag.
    monkeypatch.setattr(rl, "valkey_available", lambda: False, raising=False)

    worker_a = _Limiter(limit)
    worker_b = _Limiter(limit)

    worker_a._check_rate_limit(7)
    worker_a._check_rate_limit(7)
    with pytest.raises(SearchRateLimitError):
        worker_a._check_rate_limit(7)

    # worker_b has exhausted the same shared user budget, even though it never
    # recorded an attempt in its own process-local state.
    with pytest.raises(SearchRateLimitError):
        worker_b._check_rate_limit(7)
