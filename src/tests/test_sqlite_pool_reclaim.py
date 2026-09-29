"""
Tests for SQLite pool self-healing against leaked thread-local connections.

The Database keeps one connection per thread in thread-local storage and only
returns it to the pool when that thread explicitly calls ``close()``. Threads
that run a single query and never come back — e.g. AnyIO ``run_in_threadpool``
workers — therefore hold their connection forever. Once ``max_connections``
distinct threads have leaked a connection, every later checkout used to block
for the full connect timeout (30s default).

The pool now tracks each checked-out connection's last-usage time (refreshed
by ``touch`` from the Database layer on every query) and its owning thread,
and reclaims idle checked-out connections when the pool is exhausted (or on a
periodic reap) so a leaked worker can never permanently starve the pool.
"""

import logging
import threading
import time

import pytest

import src.utils.config as config
import src.utils.logger as logger
from src.core.database.core import Database
from src.core.database.engines.sqlite import SqliteEngine


@pytest.fixture(scope="module")
def setup_logging(tmp_path_factory):
    temp_dir = tmp_path_factory.mktemp("pool_reclaim")
    # ``logger.setup()`` mutates the process-global "AppLogger" singleton.
    # Raising it to ERROR here would otherwise filter WARNING/INFO records for
    # every later test in the same worker, so caplog-based assertions in other
    # files silently observe nothing. Restore the previous level on teardown.
    app_logger = logging.getLogger("AppLogger")
    previous_level = app_logger.level
    logger.setup(log_dir=str(temp_dir / "logs"), level="ERROR")
    try:
        yield temp_dir
    finally:
        app_logger.setLevel(previous_level)


def _make_pool(tmp_path, max_connections=8, idle_reclaim_seconds=0.05):
    """Build a tiny SqlitePool with a short reclaim threshold for fast tests."""
    engine = SqliteEngine(
        {
            "type": "sqlite",
            "path": str(tmp_path / "pool.db"),
            "connection_pool": {
                "min_connections": 2,
                "max_connections": max_connections,
                "idle_reclaim_seconds": idle_reclaim_seconds,
            },
        }
    )
    return engine.create_pool(2, max_connections)


def _leak_connections(pool, count, release):
    """Check out ``count`` connections on distinct threads that never return them.

    Mirrors AnyIO worker threads: each checks out one connection into
    thread-local storage, then goes idle forever.
    """
    holders = []
    barrier = threading.Barrier(count + 1)

    def worker():
        conn = pool.get_connection(timeout=5)
        holders.append(conn)
        barrier.wait()
        release.wait()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(count)]
    for thread in threads:
        thread.start()
    barrier.wait()
    return holders, threads


def test_exhausted_pool_reclaims_idle_leaked_connections(setup_logging, tmp_path):
    """A checkout after max_connections idle leaks succeeds promptly."""
    pool = _make_pool(tmp_path, max_connections=8, idle_reclaim_seconds=0.05)
    release = threading.Event()
    _, threads = _leak_connections(pool, 8, release)
    try:
        time.sleep(0.1)  # leaked connections go idle past the reclaim threshold
        start = time.monotonic()
        conn = pool.get_connection(timeout=2)
        elapsed = time.monotonic() - start
        # Reclaimed instead of blocking for the timeout — the leak no longer
        # starves the pool.
        assert elapsed < 0.5
        pool.return_connection(conn, force_close=True)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)


def test_exhaustion_still_blocks_when_connections_are_active(setup_logging, tmp_path):
    """Genuinely busy connections (touched recently) are NOT reclaimed."""
    pool = _make_pool(tmp_path, max_connections=4, idle_reclaim_seconds=60)
    release = threading.Event()
    holders, threads = _leak_connections(pool, 4, release)
    try:
        # Keep the leaked connections "active" by refreshing their usage time.
        for conn in holders:
            pool.touch(conn)
        with pytest.raises(TimeoutError):
            pool.get_connection(timeout=0.4)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)


def test_touch_keeps_other_threads_connection_alive(setup_logging, tmp_path):
    """A connection touched by the Database layer is not reclaimed as idle."""
    pool = _make_pool(tmp_path, max_connections=4, idle_reclaim_seconds=0.05)
    release = threading.Event()
    holders, threads = _leak_connections(pool, 1, release)
    try:
        time.sleep(0.1)  # would be idle without a touch
        pool.touch(holders[0])
        assert pool.reap_idle() == 0
        assert id(holders[0]) in pool._in_use
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)


def test_untouched_other_thread_connection_is_reclaimed(setup_logging, tmp_path):
    """An idle connection owned by another thread is reclaimed by a reap."""
    pool = _make_pool(tmp_path, max_connections=4, idle_reclaim_seconds=0.05)
    release = threading.Event()
    holders, threads = _leak_connections(pool, 1, release)
    try:
        time.sleep(0.1)
        assert pool.reap_idle() == 1
        assert id(holders[0]) not in pool._in_use
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)


def test_current_thread_connection_is_never_reclaimed(setup_logging, tmp_path):
    """The reclaiming thread's own connection is always protected."""
    pool = _make_pool(tmp_path, max_connections=4, idle_reclaim_seconds=0.05)
    conn = pool.get_connection(timeout=5)
    try:
        time.sleep(0.1)
        assert pool.reap_idle() == 0
        assert id(conn) in pool._in_use
    finally:
        pool.return_connection(conn, force_close=True)


def test_return_connection_is_idempotent_after_reclaim(setup_logging, tmp_path):
    """Returning a connection that was already reclaimed is a safe no-op."""
    pool = _make_pool(tmp_path, max_connections=2, idle_reclaim_seconds=0.05)
    release = threading.Event()
    holders, threads = _leak_connections(pool, 1, release)
    try:
        time.sleep(0.1)
        assert pool.reap_idle() == 1
        before = pool.get_stats()["total_connections"]

        # The owner thread finally closes its (already reclaimed) connection.
        pool.return_connection(holders[0], force_close=True)

        # Accounting must not double-decrement or resurrect the closed conn.
        assert pool.get_stats()["total_connections"] == before
        assert pool.get_stats()["active_connections"] == 0
        # The pool still works: a fresh checkout succeeds.
        conn = pool.get_connection(timeout=2)
        pool.return_connection(conn, force_close=True)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)


def test_stats_stay_bounded_after_many_reclaim_cycles(setup_logging, tmp_path):
    """Repeated leak + reclaim cycles never drift the accounting."""
    pool = _make_pool(tmp_path, max_connections=4, idle_reclaim_seconds=0.05)
    for cycle in range(5):
        release = threading.Event()
        _, threads = _leak_connections(pool, 4, release)
        time.sleep(0.1)
        assert pool.reap_idle() == 4
        release.set()
        for thread in threads:
            thread.join(timeout=5)
        stats = pool.get_stats()
        assert stats["total_connections"] <= stats["max_connections"]
        assert stats["total_connections"] >= 0
        assert stats["active_connections"] == 0


def test_in_transaction_connection_is_never_reclaimed(setup_logging, tmp_path):
    """A live owner inside a transaction is protected from idle reclaim."""
    pool = _make_pool(tmp_path, max_connections=2, idle_reclaim_seconds=0.05)
    release = threading.Event()
    holders, threads = _leak_connections(pool, 1, release)
    try:
        time.sleep(0.1)  # idle past the reclaim threshold
        # Owner thread is alive and inside a transaction: never reclaimed.
        pool.touch(holders[0], in_transaction=True)
        assert pool.reap_idle() == 0
        assert id(holders[0]) in pool._in_use
        # A plain (non-transactional) connection in the same state IS reclaimed
        # (touch refreshes last_used, so let it go idle again first).
        pool.touch(holders[0], in_transaction=False)
        time.sleep(0.1)
        assert pool.reap_idle() == 1
        assert id(holders[0]) not in pool._in_use
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)


def test_abandoned_transaction_connection_is_reclaimed(setup_logging, tmp_path):
    """A transaction whose owner thread has exited is reclaimed (locks freed)."""
    pool = _make_pool(tmp_path, max_connections=2, idle_reclaim_seconds=0.05)
    conn_holder = {}
    finished = threading.Event()

    def worker():
        conn = pool.get_connection(timeout=5)
        conn_holder["conn"] = conn
        pool.touch(conn, in_transaction=True)
        finished.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert finished.wait(timeout=5)
    t.join(timeout=5)  # worker exits → owner thread no longer alive

    # Wait for the thread bookkeeping to drop the dead ident from the active
    # set, then reap. CPython can keep a just-joined thread in the active set
    # briefly, so retry the reap instead of asserting a single pass.
    deadline = time.monotonic() + 5
    while pool._thread_alive(t.ident) and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)  # idle past the reclaim threshold

    reaped = 0
    while reaped == 0 and time.monotonic() < deadline:
        reaped = pool.reap_idle()
        time.sleep(0.01)
    assert reaped == 1
    assert id(conn_holder["conn"]) not in pool._in_use
    # Accounting stays consistent after reclaiming an in-transaction conn.
    assert pool.get_stats()["active_connections"] == 0


def test_owner_thread_requeries_after_pool_reclaim(setup_logging, tmp_path):
    """A thread whose connection was reclaimed transparently reconnects.

    Regression guard for the stale thread-local hazard: after the pool
    reclaims a leaked connection, the owning thread's next query must succeed
    on a fresh connection instead of failing with "Cannot operate on a closed
    database".
    """
    db_path = str(tmp_path / "requery.db")
    config.setup(
        config_path=str(tmp_path / "cfg_requery.yaml"),
        default_config={
            "database": {
                "type": "sqlite",
                "path": db_path,
                "connection_pool": {
                    "min_connections": 1,
                    "max_connections": 2,
                    "idle_reclaim_seconds": 0.05,
                },
            }
        },
    )
    Database.reset()
    db = Database()
    db.connect()
    try:
        db.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        db.execute("INSERT INTO t (id, v) VALUES (1, 'hello')")

        leaked_conn = {}
        start_querying = threading.Event()
        done = threading.Event()
        outcome = {}

        def worker():
            leaked_conn["conn"] = db._get_conn()
            start_querying.wait()
            try:
                cursor = db.execute("SELECT v FROM t WHERE id = 1")
                row = cursor.fetchone()
                outcome["row"] = tuple(row) if row is not None else None
                outcome["ok"] = True
            except Exception as e:  # pragma: no cover - assertion reports below
                outcome["err"] = repr(e)
            finally:
                done.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        for _ in range(200):
            if "conn" in leaked_conn:
                break
            time.sleep(0.01)
        assert "conn" in leaked_conn

        # Pool reclaims the worker's idle leaked connection.
        time.sleep(0.1)
        assert db._pool.reap_idle() >= 1
        assert id(leaked_conn["conn"]) not in db._pool._in_use

        # The worker's next query transparently reconnects.
        start_querying.set()
        assert done.wait(timeout=10)
        assert "err" not in outcome, outcome.get("err")
        assert outcome["row"] == ("hello",)
        t.join(timeout=5)
    finally:
        db.close()


def test_idle_reclaim_seconds_reads_from_config(setup_logging, tmp_path):
    """The engine wires connection_pool.idle_reclaim_seconds into the pool."""
    pool = _make_pool(tmp_path, max_connections=2, idle_reclaim_seconds=0.123)
    assert pool._idle_reclaim_seconds == pytest.approx(0.123)


def test_config_defaults_include_idle_reclaim_seconds(setup_logging):
    """The shipped default config documents the reclaim threshold."""
    from src.config_defaults import get_default_config

    defaults = get_default_config()
    pool_cfg = defaults["database"]["connection_pool"]
    assert pool_cfg["idle_reclaim_seconds"] > 0
