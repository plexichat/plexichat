"""Regression tests for two shared-store (multi-worker) scaling races.

1. A token-bucket spend must never move ``last_update`` backwards. A concurrent
   worker's refill can persist a later clock between our refill re-read and our
   absolute spend write; writing our older clock regresses the refill clock and
   lets the next request re-accrue the same elapsed time (free tokens).
2. The public DB mutators (increment/get_and_set/add_to_list/trim_list) must be
   serialised across workers. They used a plain get->mutate->set, so concurrent
   increments of a shared abuse counter lost updates and multiplied the
   attacker's budget by the number of contending workers.
"""

import threading
import time

from src.core.ratelimit.storage.database import DatabaseStorage


def _run_concurrently(fn, n: int = 16) -> None:
    """Start ``n`` workers at once and wait for all of them."""
    barrier = threading.Barrier(n)
    threads = []

    def worker() -> None:
        barrier.wait()
        fn()

    for _ in range(n):
        thread = threading.Thread(target=worker)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), "a worker deadlocked"


class TestDatabaseMutatorsAreAtomic:
    def test_concurrent_increments_are_not_lost(self, db) -> None:
        storage = DatabaseStorage(db)
        _run_concurrently(lambda: storage.increment("scaling:count", "hits", 1))
        assert (storage.get_bucket("scaling:count") or {})["hits"] == 16

    def test_concurrent_increment_returns_committed_value(self, db) -> None:
        """Each caller must see the value its CAS actually committed."""
        storage = DatabaseStorage(db)
        returned: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            value = storage.increment("scaling:ret", "hits", 1)
            with lock:
                returned.append(value)

        _run_concurrently(worker)
        # Exactly one caller observed each value 1..16, with no duplicates.
        assert sorted(returned) == list(range(1, 17))

    def test_concurrent_list_appends_are_not_lost(self, db) -> None:
        storage = DatabaseStorage(db)

        def worker(idx: int) -> None:
            storage.add_to_list("scaling:list", "events", idx, max_size=100)

        barrier = threading.Barrier(16)

        def start(idx: int) -> None:
            barrier.wait()
            worker(idx)

        threads = [threading.Thread(target=start, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert all(not thread.is_alive() for thread in threads)

        state = storage.get_bucket("scaling:list") or {}
        assert len(state["events"]) == 16
        assert sorted(state["events"]) == list(range(16))

    def test_get_and_set_is_atomic(self, db) -> None:
        storage = DatabaseStorage(db)
        storage.set_bucket("scaling:gas", {"flag": 0}, ttl=60)
        previous = storage.get_and_set("scaling:gas", "flag", 1)
        assert previous == 0
        assert (storage.get_bucket("scaling:gas") or {})["flag"] == 1


class TestTokenBucketClockNeverRegresses:
    def test_spend_does_not_rewind_a_later_refill_clock(self, db) -> None:
        """A spend must keep the greatest clock, not overwrite with an older one.

        Simulate a concurrent worker that already persisted a later
        ``last_update`` (clock skew / interleaved refill). The absolute spend
        write used to reset it to the caller's older ``now``, regressing the
        refill clock and minting free tokens on the next request.
        """
        storage = DatabaseStorage(db)
        future_clock = time.time() + 3600
        storage.set_bucket(
            "scaling:tb",
            {"tokens": 10.0, "last_update": future_clock},
            ttl=86400,
        )

        allowed, _, _ = storage.eval_token_bucket(
            "scaling:tb", capacity=10, refill_rate=1.0, cost=1, ttl=86400
        )
        assert allowed is True

        state = storage.get_bucket("scaling:tb") or {}
        assert float(state["last_update"]) >= future_clock, (
            "spend regressed the refill clock: next request gets a double refill"
        )
        assert float(state["tokens"]) == 9.0
