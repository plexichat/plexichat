"""Database performance regression guards (query count and row scans).

This module is the standing budget for the hot data-access paths. It is
designed to FAIL LOUDLY if a future change:

* turns a batched key lookup back into an N+1 loop (query_count grows with n),
* makes a hot query scan a table or sort in a temporary B-tree again, or
* reintroduces per-call SQL preparation on a repeated statement.

The budgets are expressed in query counts and EXPLAIN QUERY PLAN / VM-step
measurements, not wall-clock time, so they stay stable on a loaded runner.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import utils.config as config
from src.core.database.core import Database

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def perf_db(tmp_path):
    Database.reset()
    config.setup(
        config_path=str(tmp_path / "config.yaml"),
        default_config={
            "database": {
                "type": "sqlite",
                "path": str(tmp_path / "perf.db"),
            }
        },
    )
    Database.reset()
    db = Database()
    db.connect()
    getattr(db, "execute")("CREATE TABLE perf_items (id INTEGER PRIMARY KEY, value TEXT)")
    rows = [(item_id, f"v{item_id}") for item_id in range(1, 301)]
    getattr(db, "execute_many")(
        "INSERT INTO perf_items (id, value) VALUES (?, ?)", rows
    )
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    yield db
    db.close()
    Database.reset()


def _query_count(db):
    return db.get_request_metrics()["query_count"]


# ---------------------------------------------------------------------------
# Query-count budget
# ---------------------------------------------------------------------------


class TestQueryCountBudget:
    def test_batch_lookup_is_one_statement_for_a_page(self, perf_db):
        ids = list(range(1, 41))
        perf_db.fetch_many_by_ids("perf_items", ids)
        assert _query_count(perf_db) == 1, (
            "query-count regression: a 40-id batch must be one IN() query, "
            f"got {_query_count(perf_db)}"
        )

    def test_query_count_does_not_grow_with_page_size(self, perf_db):
        perf_db.fetch_many_by_ids("perf_items", list(range(1, 11)))
        small = _query_count(perf_db)
        perf_db.reset_request_metrics()
        perf_db.fetch_many_by_ids("perf_items", list(range(1, 101)))
        large = _query_count(perf_db)
        # An N+1 regression would make large ≈ 10 * small; a bounded batch
        # keeps both at exactly one statement.
        assert small == 1 and large == 1, (
            f"query-count regression: 10 ids -> {small}, 100 ids -> {large}"
        )

    def test_chunking_keeps_query_count_bounded(self, perf_db):
        perf_db.fetch_many_by_ids("perf_items", list(range(1, 2001)))
        count = _query_count(perf_db)
        assert count == 3, f"expected ceil(2000/900) == 3 chunks, got {count}"


# ---------------------------------------------------------------------------
# Row-scan budget
# ---------------------------------------------------------------------------

_INDEX_TABLE_DDL = (
    "CREATE TABLE srv_audit_log (id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL, "
    "action TEXT NOT NULL, created_at INTEGER NOT NULL)",
    "CREATE TABLE msg_messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, "
    "author_id INTEGER, created_at INTEGER)",
    "CREATE TABLE srv_scheduled_events (id INTEGER PRIMARY KEY, server_id INTEGER, "
    "status TEXT, start_time INTEGER)",
)
_AUDIT_DDL = _INDEX_TABLE_DDL[0]
_AUDIT_QUERY = (
    "SELECT * FROM srv_audit_log WHERE server_id = 7 ORDER BY created_at DESC LIMIT 50"
)


def _load_migration_007():
    import src.core.migrations.migrations as pkg

    path = Path(pkg.__file__).parent / "007_hot_lookup_composite_indexes.py"
    spec = importlib.util.spec_from_file_location("migration_007_perf", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plan(conn, query):
    return "\n".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query))


def _vm_steps(conn, query):
    """Count SQLite VM ops for a query -- a proxy for rows examined."""
    counter = [0]

    def on_step():
        counter[0] += 1
        return 0

    conn.set_progress_handler(on_step, 1)
    try:
        conn.execute(query).fetchall()
    finally:
        conn.set_progress_handler(None, 1)
    return counter[0]


class TestRowScanBudget:
    def test_hot_queries_are_index_seeks(self):
        conn = sqlite3.connect(":memory:")
        try:
            for ddl in _INDEX_TABLE_DDL:
                conn.execute(ddl)
            migration = _load_migration_007()
            for name, columns in migration.INDEXES:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {columns}")

            plan = _plan(conn, _AUDIT_QUERY)
            assert "idx_srv_audit_server_created" in plan
            assert "SCAN " not in plan
            assert "USE TEMP B-TREE FOR ORDER BY" not in plan
        finally:
            conn.close()

    def test_composite_index_reduces_rows_examined(self):
        # 2000 rows spread over 20 servers. Without the composite index the
        # planner walks every row for one server and sorts them; with it, the
        # LIMIT is satisfied directly from the index.
        def build(with_composite):
            conn = sqlite3.connect(":memory:")
            conn.execute(_AUDIT_DDL)
            conn.execute(
                "CREATE INDEX idx_srv_audit_server ON srv_audit_log(server_id)"
            )
            if with_composite:
                conn.execute(
                    "CREATE INDEX idx_srv_audit_server_created "
                    "ON srv_audit_log(server_id, created_at)"
                )
            conn.executemany(
                "INSERT INTO srv_audit_log (server_id, action, created_at) "
                "VALUES (?, ?, ?)",
                [(i % 20, "x", i) for i in range(2000)],
            )
            return conn

        baseline = build(False)
        indexed = build(True)
        try:
            unindexed_steps = _vm_steps(baseline, _AUDIT_QUERY)
            indexed_steps = _vm_steps(indexed, _AUDIT_QUERY)
            assert indexed_steps < unindexed_steps, (
                f"row-scan regression: indexed plan examined {indexed_steps} "
                f"VM steps vs {unindexed_steps} without the composite index"
            )
        finally:
            baseline.close()
            indexed.close()


# ---------------------------------------------------------------------------
# Preparation budget
# ---------------------------------------------------------------------------


class TestPreparationBudget:
    def test_repeated_statement_is_prepared_once(self, perf_db):
        db = perf_db
        db._statement_cache.reset_stats()
        for item_id in range(1, 31):
            db.fetch_one("SELECT value FROM perf_items WHERE id = ?", (item_id,))
        stats = db.get_statement_cache_stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 29
