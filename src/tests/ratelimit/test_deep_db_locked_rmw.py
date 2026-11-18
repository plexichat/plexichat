"""Regression: DatabaseStorage read-modify-write must not lose updates.

The database backend cannot serialise workers with a lock token (its
``acquire_lock`` returns a constant), so the inherited get->mutate->set path
let two workers read the same counter and both write it back. For legacy
(fixed/sliding window, hourly/daily) limits that spends one unit of quota
twice. ``update_bucket`` now uses a compare-and-swap against the exact row it
read and retries on contention; these tests pin that invariant.
"""

import threading

from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage.database import DatabaseStorage


def _increment(storage: DatabaseStorage, key: str, n: int) -> int:
    barrier = threading.Barrier(n)

    def worker(idx: int) -> None:
        barrier.wait()
        storage.update_bucket(
            key, lambda state: state.update(counter=state.get("counter", 0) + 1)
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads)
    return int((storage.get_bucket(key) or {}).get("counter", 0))


class TestDatabaseAtomicReadModifyWrite:
    def test_concurrent_increments_are_not_lost(self, db):
        storage = DatabaseStorage(db)
        assert _increment(storage, "deep-db:rmw", 16) == 16

    def test_legacy_fixed_window_limit_exact_under_concurrency(self, db):
        storage = DatabaseStorage(db)
        manager = RateLimitManager(
            storage_backend=storage,
            route_configs={
                "GET /t": RateLimitConfig(
                    requests=5,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
                )
            },
            enable_global_limit=False,
        )

        n = 16
        barrier = threading.Barrier(n)
        allowed: list = [None] * n

        def worker(idx: int) -> None:
            barrier.wait()
            allowed[idx] = manager.check_rate_limit(user_id=7, route="GET /t").allowed

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert all(not thread.is_alive() for thread in threads)
        assert sum(1 for result in allowed if result is True) == 5
        state = storage.get_bucket("route:u:7:r:GET /t")
        assert state is not None and state["request_count"] == 5

    def test_custom_counter_value_is_preserved(self, db):
        storage = DatabaseStorage(db)

        def bump(state: dict) -> None:
            state["counter"] = state.get("counter", 0) + 1

        storage.update_bucket("deep-db:counter", bump)
        storage.update_bucket("deep-db:counter", bump)
        state = storage.get_bucket("deep-db:counter")
        assert state is not None and state["counter"] == 2
