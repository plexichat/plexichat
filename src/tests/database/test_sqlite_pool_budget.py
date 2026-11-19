"""Regression tests for fleet-bounded SQLite pools and creation cleanup.

``PostgresEngine`` divides the fleet-wide connection budget across
``--scale backend=N`` replicas and clamps the acquire timeout, but
``SqliteEngine`` passed the raw configured min/max and raw ``connect_timeout``
straight into its pool. Every replica therefore built a full-size pool (each
SQLite handle reserves mmap/page-cache memory) and a hostile timeout reached
the pool unchecked. A failure while opening warm connections also leaked the
handles already opened. All three are now bounded and cleaned up.
"""

import pytest

from src.core.database.engines.sqlite import SqliteEngine
from src.core.database.pool_budget import (
    DEFAULT_TOTAL_CONNECTION_BUDGET,
    MAX_ACQUIRE_TIMEOUT_SECONDS,
)


class _DummyConn:
    """Minimal DB-API-shaped connection that records whether it was closed."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _engine(pool_config=None):
    return SqliteEngine({"connection_pool": pool_config or {}})


class TestSqlitePoolBounds:
    def test_replicas_share_one_fleet_budget(self, monkeypatch):
        engine = _engine({"min_connections": 2, "max_connections": 100})
        opened = []

        def make():
            conn = _DummyConn()
            opened.append(conn)
            return conn

        monkeypatch.setattr(engine, "_create_single_connection", make)
        monkeypatch.setenv("BACKEND_REPLICAS", "4")

        pool = engine.create_pool(2, 100)
        try:
            stats = pool.get_stats()
            # 90 (default fleet budget) / 4 = 22, not the full 100.
            assert stats["max_connections"] == DEFAULT_TOTAL_CONNECTION_BUDGET // 4
            assert stats["min_connections"] == 2
            assert len(opened) == 2  # warm connections shrink with the ceiling
        finally:
            pool.close()

    def test_hostile_sizes_are_bounded_and_ordered(self, monkeypatch):
        engine = _engine(
            {
                "min_connections": -5,
                "max_connections": "not-a-number",
                "connect_timeout": 10_000,
            }
        )

        def make():
            return _DummyConn()

        monkeypatch.setattr(engine, "_create_single_connection", make)

        pool = engine.create_pool(-5, "not-a-number")
        try:
            stats = pool.get_stats()
            assert 0 <= stats["min_connections"] <= stats["max_connections"]
            assert stats["max_connections"] <= DEFAULT_TOTAL_CONNECTION_BUDGET
        finally:
            pool.close()

    def test_connect_clamps_the_acquire_timeout(self, monkeypatch):
        engine = _engine({"connect_timeout": 10_000})
        pool = engine.create_pool(0, 2)
        seen = {}

        def fake_get(timeout):
            seen["timeout"] = timeout
            return _DummyConn()

        monkeypatch.setattr(pool, "get_connection", fake_get)
        try:
            engine.connect(pool)
            assert seen["timeout"] == MAX_ACQUIRE_TIMEOUT_SECONDS
        finally:
            pool.close()


class TestSqliteCreationCleanup:
    def test_failed_warm_up_closes_connections_already_opened(self, monkeypatch):
        engine = _engine({"min_connections": 3, "max_connections": 10})
        opened = []
        attempts = {"n": 0}

        def make():
            attempts["n"] += 1
            if attempts["n"] == 3:  # third warm connection fails during startup
                raise RuntimeError("disk full")
            conn = _DummyConn()
            opened.append(conn)
            return conn

        monkeypatch.setattr(engine, "_create_single_connection", make)

        with pytest.raises(RuntimeError, match="disk full"):
            engine.create_pool(3, 10)

        # Both handles opened before the failure must be closed, not leaked.
        assert len(opened) == 2
        assert all(conn.closed for conn in opened)
