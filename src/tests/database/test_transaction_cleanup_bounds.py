"""Regression tests for transaction-cleanup leaks and pool bounds.

Under high concurrency a worker that hits a COMMIT/ROLLBACK error must not keep
its pooled connection checked out and flagged ``in_transaction``. Two such
failures per worker were enough to pin a cores-sized SQLite pool and make every
other request thread block until the acquisition timeout. The outermost
commit/rollback paths now unwind markers and return (or discard) the connection
on *every* exit path.

The harness tests exercise ``DatabaseTransactionMixin`` directly with a fake
connection so each error path is deterministic; the integration test mirrors
the real single-connection-pool leak that motivated the fix.
"""

import sqlite3
from types import SimpleNamespace

import pytest

import utils.config as config
from src.core.database.core import Database
from src.core.database.core.transactions import DatabaseTransactionMixin
from src.core.database.engines.pool import MAX_POOL_SIZE, ConnectionPool
from src.core.security.hardening import HardeningViolation


class _FakeConn:
    """Minimal DB-API connection with injectable COMMIT/ROLLBACK failures."""

    def __init__(self, commit_error=None, rollback_error=None):
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.in_transaction = False
        self.executed = []
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, *args, **kwargs):
        statement = str(sql).strip().upper()
        self.executed.append(statement)
        if statement == "BEGIN":
            self.in_transaction = True
        elif statement == "COMMIT":
            if self.commit_error is not None:
                raise self.commit_error
            self.in_transaction = False
        elif statement == "ROLLBACK":
            if self.rollback_error is not None:
                raise self.rollback_error
            self.in_transaction = False
        return self

    def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.in_transaction = False

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error
        self.in_transaction = False

    def cursor(self):
        return self

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self):
        self.force_closed = []

    def close_connection(self, conn, pool, params):
        if params and params.get("close"):
            self.force_closed.append(conn)


class _TxHarness(DatabaseTransactionMixin):
    """Exercise the transaction mixin without a real driver or thread locals."""

    def __init__(self, conn, engine=None):
        self.type = "sqlite"
        self.engine = engine
        self._pool = object()
        self._local = SimpleNamespace(
            connection=conn,
            transaction_depth=0,
            in_transaction=False,
        )
        self.touch_calls = []
        self.release_calls = 0

    @property
    def transaction_depth(self):
        return self._local.transaction_depth

    @transaction_depth.setter
    def transaction_depth(self, value):
        self._local.transaction_depth = value

    @property
    def in_transaction(self):
        return self._local.in_transaction

    @in_transaction.setter
    def in_transaction(self, value):
        self._local.in_transaction = value

    def _invalidate_query_cache(self):
        pass

    def _get_conn(self):
        return self._local.connection

    def _touch_pool(self, conn, in_transaction=False):
        self.touch_calls.append(bool(in_transaction))

    def _release_conn(self):
        self.release_calls += 1
        self._local.connection = None


class TestOutermostCommitCleanup:
    def test_no_transaction_commit_releases_connection(self):
        conn = _FakeConn(
            commit_error=sqlite3.OperationalError(
                "cannot commit - no transaction is active"
            )
        )
        harness = _TxHarness(conn)
        harness.begin_transaction()
        conn.in_transaction = False  # transaction ended behind the DB layer

        harness.commit()  # terminal "no transaction" case is swallowed

        assert harness.transaction_depth == 0
        assert harness.in_transaction is False
        assert harness._local.connection is None
        assert harness.release_calls == 1
        assert harness.touch_calls[-1] is False

    def test_unexpected_commit_error_cleans_up_and_reraises(self):
        conn = _FakeConn(commit_error=RuntimeError("boom"))
        harness = _TxHarness(conn)
        harness.begin_transaction()

        with pytest.raises(RuntimeError, match="boom"):
            harness.commit()

        assert harness.transaction_depth == 0
        assert harness.in_transaction is False
        assert harness._local.connection is None
        assert harness.release_calls == 1
        assert conn.rollbacks == 1  # open transaction was aborted first
        assert conn.in_transaction is False
        assert harness.touch_calls[-1] is False

    def test_failed_abort_discards_connection_instead_of_pooling_it(self):
        conn = _FakeConn(
            commit_error=RuntimeError("boom"),
            rollback_error=RuntimeError("rollback boom"),
        )
        engine = _FakeEngine()
        harness = _TxHarness(conn, engine=engine)
        harness.begin_transaction()

        with pytest.raises(RuntimeError, match="boom"):
            harness.commit()

        # The transaction could not be aborted, so the indeterminate connection
        # is force-closed and removed from pool accounting, not returned clean.
        assert engine.force_closed == [conn]
        assert harness._local.connection is None
        assert harness.release_calls == 1
        assert harness.transaction_depth == 0


class TestOutermostRollbackCleanup:
    def test_no_transaction_rollback_releases_connection(self):
        conn = _FakeConn(
            rollback_error=sqlite3.OperationalError(
                "cannot rollback - no transaction is active"
            )
        )
        harness = _TxHarness(conn)
        harness.begin_transaction()
        conn.in_transaction = False

        harness.rollback()  # terminal "no transaction" case is swallowed

        assert harness.transaction_depth == 0
        assert harness.in_transaction is False
        assert harness._local.connection is None
        assert harness.release_calls == 1

    def test_failed_rollback_cleans_up_and_reraises(self):
        conn = _FakeConn(rollback_error=RuntimeError("rb boom"))
        harness = _TxHarness(conn)
        harness.begin_transaction()

        with pytest.raises(RuntimeError, match="rb boom"):
            harness.rollback()

        assert harness.transaction_depth == 0
        assert harness.in_transaction is False
        assert harness._local.connection is None
        assert harness.release_calls == 1
        assert harness.touch_calls[-1] is False


class TestBounds:
    def test_pool_rejects_unbounded_max_size(self):
        # A hostile/mis-typed max must be refused at construction rather than
        # opening an unbounded number of sockets on the first request.
        with pytest.raises(HardeningViolation):
            ConnectionPool(
                create_conn=lambda: _FakeConn(),
                min_size=0,
                max_size=10**9,
            )

    def test_pool_accepts_size_at_the_documented_ceiling(self):
        pool = ConnectionPool(
            create_conn=lambda: _FakeConn(),
            min_size=0,
            max_size=MAX_POOL_SIZE,
        )
        try:
            assert pool.get_stats()["max_connections"] == MAX_POOL_SIZE
        finally:
            pool.close()


class TestRealDatabaseCleanup:
    def test_failed_commit_releases_single_connection_pool(self, tmp_path):
        """A real single-connection pool must not be pinned by a commit error."""
        Database.reset()
        config.setup(
            config_path=str(tmp_path / "config.yaml"),
            default_config={
                "database": {
                    "type": "sqlite",
                    "path": str(tmp_path / "cleanup.db"),
                    "connection_pool": {
                        "min_connections": 1,
                        "max_connections": 1,
                        "cache_size_kb": -1024,
                        "mmap_size": 1048576,
                        "journal_size_limit": 1048576,
                        "idle_reclaim_seconds": 99999,
                    },
                }
            },
        )
        Database.reset()
        db = Database()
        try:
            db.begin_transaction()
            assert db.connection is not None
            # End the transaction behind the DB layer so COMMIT fails with the
            # terminal "no transaction is active" error.
            db.connection.execute("ROLLBACK")
            db.commit()

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
