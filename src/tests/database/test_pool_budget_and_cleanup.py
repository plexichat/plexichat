"""Regression tests for bounded, worker-aware pool sizing and error cleanup.

Before these fixes:

* every ``docker compose up --scale backend=N`` replica built a pool with the
  full configured ``max_connections`` (100), so N workers tried to open N*100
  sockets against one PostgreSQL instance (default max_connections=100). The
  surplus were refused and workers piled up waiting on an exhausted pool.
* a failed nested ``commit()``/``rollback()`` reset the in-memory transaction
  flags but left the connection checked out and marked ``in_transaction`` in
  the pool, so repeated failures pinned pool capacity until the idle reaper
  eventually reclaimed it.

Both paths are now bounded and clean up after themselves.
"""

import pytest

import utils.config as config
from src.core.database.core import Database
from src.core.database.engines.postgres import PostgresEngine
from src.core.database.pool_budget import (
    DEFAULT_TOTAL_CONNECTION_BUDGET,
    MAX_ACQUIRE_TIMEOUT_SECONDS,
    MAX_REPLICAS,
    resolve_connect_timeout,
    resolve_pool_limits,
)


class TestPoolLimits:
    def test_scaled_replicas_share_one_fleet_budget(self):
        cfg = {
            "connection_pool": {
                "min_connections": 5,
                "max_connections": 100,
            }
        }
        limits = resolve_pool_limits(cfg, env={"BACKEND_REPLICAS": "4"})
        # 90 (default fleet budget, headroom under PostgreSQL's 100) / 4 = 22.
        assert limits.max_connections == DEFAULT_TOTAL_CONNECTION_BUDGET // 4
        # Eager warm connections shrink with the ceiling.
        assert limits.min_connections == 5

    def test_single_worker_is_bounded_by_fleet_budget(self):
        cfg = {"connection_pool": {"min_connections": 5, "max_connections": 100}}
        limits = resolve_pool_limits(cfg, env={})
        assert limits.max_connections == DEFAULT_TOTAL_CONNECTION_BUDGET
        assert limits.min_connections == 5

    @pytest.mark.parametrize("replicas", ["-1", "0", "abc", ""])
    def test_invalid_replica_count_is_not_unbounded(self, replicas):
        cfg = {"connection_pool": {"min_connections": 5, "max_connections": 100}}
        limits = resolve_pool_limits(cfg, env={"BACKEND_REPLICAS": replicas})
        # Invalid/empty values fall back to a single worker, never to "no cap".
        assert 1 <= limits.min_connections <= limits.max_connections
        assert limits.max_connections == DEFAULT_TOTAL_CONNECTION_BUDGET

    def test_extreme_replica_count_still_leaves_a_usable_pool(self):
        cfg = {"connection_pool": {"min_connections": 5, "max_connections": 100}}
        limits = resolve_pool_limits(cfg, env={"BACKEND_REPLICAS": "999999"})
        assert limits.max_connections >= 2
        # Replica count is capped so the division never reaches zero.
        assert MAX_REPLICAS >= 1

    def test_custom_fleet_budget_and_min_max_ordering(self):
        cfg = {
            "connection_pool": {
                "min_connections": 50,
                "max_connections": 5,
                "total_connection_budget": 40,
            }
        }
        limits = resolve_pool_limits(cfg, env={"BACKEND_REPLICAS": "2"})
        # 40 / 2 = 20, capped by the configured max of 5; min is clamped down
        # so a misconfiguration cannot fail pool construction with min > max.
        assert limits.max_connections == 5
        assert limits.min_connections == 5

    def test_hostile_pool_values_are_bounded(self):
        cfg = {
            "connection_pool": {
                "min_connections": "not-a-number",
                "max_connections": -5,
            }
        }
        limits = resolve_pool_limits(cfg, env={})
        assert limits.min_connections <= limits.max_connections
        assert limits.max_connections <= DEFAULT_TOTAL_CONNECTION_BUDGET

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (10, 10),
            (10_000, MAX_ACQUIRE_TIMEOUT_SECONDS),
            (-5, 0.0),
            ("not-a-number", 30.0),
            (None, 30.0),
        ],
    )
    def test_connect_timeout_is_bounded(self, raw, expected):
        cfg = {"connection_pool": {"connect_timeout": raw}}
        assert resolve_connect_timeout(cfg, default=30.0) == expected

    def test_postgres_engine_builds_a_fleet_bounded_pool(self, monkeypatch):
        created = []

        class _DummyConn:
            def close(self):
                pass

        engine = PostgresEngine(
            {
                "connection_pool": {
                    "min_connections": 3,
                    "max_connections": 100,
                    "connect_timeout": 5,
                }
            }
        )
        monkeypatch.setenv("BACKEND_REPLICAS", "3")
        monkeypatch.setattr(
            engine,
            "_create_single_connection",
            lambda: created.append(1) or _DummyConn(),
        )
        pool = engine.create_pool(3, 100)
        try:
            stats = pool.get_stats()
            assert stats["max_connections"] == DEFAULT_TOTAL_CONNECTION_BUDGET // 3
            assert stats["min_connections"] == 3
            assert len(created) == 3  # min connections opened eagerly
        finally:
            pool.close()


class TestTransactionErrorCleanup:
    def test_failed_nested_commit_returns_connection_to_pool(self, tmp_path):
        """A failed SAVEPOINT release must not strand a pooled connection."""
        Database.reset()
        config.setup(
            config_path=str(tmp_path / "config.yaml"),
            default_config={
                "database": {
                    "type": "sqlite",
                    "path": str(tmp_path / "cleanup.db"),
                    "connection_pool": {
                        "min_connections": 0,
                        "max_connections": 1,
                    },
                }
            },
        )
        Database.reset()
        db = Database()
        try:
            db.begin_transaction()  # depth 1: BEGIN
            db.begin_transaction()  # depth 2: SAVEPOINT sp_2
            conn = db.connection
            assert conn is not None
            # Release the savepoint behind the DB layer's back so the
            # RELEASE SAVEPOINT issued by commit() fails.
            conn.execute("RELEASE SAVEPOINT sp_2")

            with pytest.raises(Exception):
                db.commit()

            # The error path must unwind all state and hand the single pooled
            # connection back so another worker can acquire it immediately.
            assert db.connection is None
            assert db.transaction_depth == 0
            assert db.in_transaction is False
            acquired = db._pool.get_connection(timeout=0.1)
            assert acquired is not None
            db._pool.return_connection(acquired)
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
            Database.reset()
