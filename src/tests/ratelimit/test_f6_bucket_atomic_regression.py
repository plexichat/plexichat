"""
F6 regression: DatabaseStorage token-bucket consume must be atomic.

The vulnerable implementation did SELECT tokens ... then a blind
``UPDATE ... SET tokens = ?``. Under concurrent consumers (multi-worker
fallback when Valkey is absent) two workers read the same balance and
both spends succeeded (lost update), so a 5-token bucket admitted ~8 of
10 parallel requests.

The fix performs the deduction as a single conditional
``UPDATE ... WHERE key = ? AND tokens >= ?`` (compare-and-set) and only
counts the request as allowed when ``cursor.rowcount > 0``; losers
re-read and retry. This test hammers one small bucket from 10 threads
sharing a single temp-file sqlite Database and asserts no over-admit.

Would FAIL on pre-fix code (allowed > capacity) and PASSES after.
"""

import threading

from src.core.ratelimit.storage.database import DatabaseStorage


def _hammer(storage, key, capacity, out, idx, barrier):
    barrier.wait()
    out[idx] = storage.eval_token_bucket(
        key, capacity=capacity, refill_rate=0.0, cost=1, ttl=86400
    )


class TestF6BucketAtomicRegression:
    def test_parallel_consume_never_over_admits(self, db):
        """10 threads vs a 5-token bucket: at most 5 may be allowed."""
        storage = DatabaseStorage(db)
        key = "f6:race:bucket"
        capacity = 5
        n_threads = 10

        barrier = threading.Barrier(n_threads)
        out = [None] * n_threads
        threads = [
            threading.Thread(
                target=_hammer, args=(storage, key, capacity, out, i, barrier)
            )
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert all(t.is_alive() is False for t in threads)
        assert all(r is not None for r in out)

        allowed = sum(1 for r in out if r[0] is True)
        # No refill: exactly `capacity` admits. ``<=`` keeps the test
        # robust: fail-closed denies on transient lock errors may only
        # *reduce* admits, never inflate them (the unsafe direction).
        assert allowed <= capacity
        # And the limiter must still admit real quota (not fail everything).
        assert allowed >= 1

        # Token conservation (strict, deterministic): with refill_rate=0
        # every allowed consume decrements the stored balance by exactly
        # 1 via a relative UPDATE, and denied consumes write nothing, so
        # stored + allowed == capacity EXACTLY — even when lock
        # contention causes fail-closed denies. The vulnerable absolute
        # -write code breaks this (lost updates: two workers both land
        # `capacity - 1`, so stored + allowed > capacity).
        state = storage.get_bucket(key)
        assert state is not None
        assert state["tokens"] + allowed == capacity

    def test_sequential_consume_exact_quota(self, db):
        """Sanity: 5 sequential consumes on a 5-token bucket, then deny."""
        storage = DatabaseStorage(db)
        key = "f6:sequential:bucket"
        results = [
            storage.eval_token_bucket(
                key, capacity=5, refill_rate=0.0, cost=1, ttl=86400
            )
            for _ in range(7)
        ]
        allowed = [r[0] for r in results]
        assert allowed == [True] * 5 + [False] * 2
        # Denied responses must report zero remaining, never success.
        for r in results[5:]:
            assert r[1] == 0

    def test_cost_greater_than_one_atomic(self, db):
        """Concurrent cost=3 consumes on a 5-token bucket: at most one wins."""
        storage = DatabaseStorage(db)
        key = "f6:cost3:bucket"
        n_threads = 5
        barrier = threading.Barrier(n_threads)
        out = [None] * n_threads

        def _hammer3(idx):
            barrier.wait()
            out[idx] = storage.eval_token_bucket(
                key, capacity=5, refill_rate=0.0, cost=3, ttl=86400
            )

        threads = [
            threading.Thread(target=_hammer3, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert all(r is not None for r in out)
        allowed = sum(1 for r in out if r[0] is True)
        # 5 tokens afford exactly one cost-3 spend (3 spent, 2 left < 3).
        assert allowed == 1
