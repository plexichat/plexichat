"""Focused regression test for fair connection acquisition and wait metrics.

Before: a saturated pool used ``Condition.notify()``, which wakes an arbitrary
waiter. Under sustained contention a late arrival could repeatedly beat a
thread that had been parked longer, and pool-wait time was recorded nowhere
(``DatabaseMonitor.record_pool_wait`` was never called). After: waiters queue
FIFO, only the head may consume a returned connection, and every completed
wait is counted, timed, surfaced in ``get_stats()`` and forwarded to the
monitor.
"""

import threading
import time

import pytest

import utils.config as config
from src.core.database.core import Database
from src.core.database.engines.pool import ConnectionPool


class _Conn:
    pass


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class TestFairAcquisition:
    def test_waiters_are_served_in_arrival_order(self):
        pool = ConnectionPool(lambda: _Conn(), min_size=0, max_size=1, label="test")
        first = pool.get_connection(timeout=1)
        acquired = []
        release = {"B": threading.Event(), "C": threading.Event()}

        def waiter(name):
            conn = pool.get_connection(timeout=3)
            acquired.append(name)
            release[name].wait(3)
            pool.return_connection(conn)

        threads = [threading.Thread(target=waiter, args=(name,)) for name in ("B", "C")]
        try:
            threads[0].start()
            assert _wait_for(lambda: len(pool._wait_queue) == 1)
            threads[1].start()
            assert _wait_for(lambda: len(pool._wait_queue) == 2)
            assert list(pool._wait_queue)[0] is not list(pool._wait_queue)[1]

            # One connection returns: only B (the head) may take it.
            pool.return_connection(first)
            assert _wait_for(lambda: "B" in acquired)
            assert acquired == ["B"], f"C overtook B: {acquired}"

            # B returns its connection; now C is head and may proceed.
            release["B"].set()
            assert _wait_for(lambda: "C" in acquired)
            assert acquired == ["B", "C"]
        finally:
            release["B"].set()
            release["C"].set()
            for thread in threads:
                thread.join(timeout=3)

    def test_timed_out_waiter_leaves_no_queue_slot(self):
        pool = ConnectionPool(lambda: _Conn(), min_size=0, max_size=1)
        first = pool.get_connection(timeout=1)
        try:
            with pytest.raises(TimeoutError):
                pool.get_connection(timeout=0.05)
            stats = pool.get_stats()
            assert stats["wait_queue_depth"] == 0
            assert stats["wait_count"] == 1
        finally:
            pool.return_connection(first)
        assert pool.get_connection(timeout=1) is not None

    def test_wait_metrics_and_observer(self):
        observed = []
        pool = ConnectionPool(lambda: _Conn(), min_size=0, max_size=1)
        pool.set_wait_observer(observed.append)
        first = pool.get_connection(timeout=1)

        done = threading.Event()

        def waiter():
            conn = pool.get_connection(timeout=3)
            pool.return_connection(conn)
            done.set()

        thread = threading.Thread(target=waiter)
        try:
            thread.start()
            assert _wait_for(lambda: len(pool._wait_queue) == 1)
            time.sleep(0.02)  # make the wait measurable
            pool.return_connection(first)
            assert done.wait(3)
        finally:
            thread.join(timeout=3)

        assert len(observed) == 1
        assert observed[0] > 0
        stats = pool.get_stats()
        assert stats["wait_count"] == 1
        assert stats["avg_wait_time"] > 0
        assert stats["max_wait_time"] >= stats["avg_wait_time"]
        assert stats["wait_queue_depth"] == 0


class TestMonitorWaitIntegration:
    def test_monitor_receives_pool_wait_duration(self, tmp_path):
        Database.reset()
        config.setup(
            config_path=str(tmp_path / "config.yaml"),
            default_config={
                "database": {
                    "type": "sqlite",
                    "path": str(tmp_path / "pool.db"),
                    "connection_pool": {
                        "min_connections": 0,
                        "max_connections": 1,
                    },
                }
            },
        )
        Database.reset()
        db = Database()
        db.connect()  # main thread holds the single pooled connection
        done = threading.Event()

        def waiter():
            worker = Database()
            worker.connect()
            worker.close()
            done.set()

        thread = threading.Thread(target=waiter)
        try:
            thread.start()
            assert _wait_for(lambda: len(db._pool._wait_queue) == 1)
            db._release_conn()
            assert done.wait(3)
            waits = list(db.monitor._connection_pool_waits)
        finally:
            thread.join(timeout=3)
            try:
                db._release_conn()
            except Exception:
                pass
            db.close()
            Database.reset()

        assert len(waits) >= 1
        assert waits[-1][1] > 0
