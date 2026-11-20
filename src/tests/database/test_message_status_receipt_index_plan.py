"""Query-plan and result-parity guards for the message-status receipt index (013).

An index is only useful if the planner actually picks it. These tests apply the
real migration to an in-memory SQLite database and read ``EXPLAIN QUERY PLAN``
for the two hot read-receipt queries it is meant to serve. They fail when the
index disappears, when a plan regresses to a temporary sort B-tree, when adding
the index changes the rows a query returns, or when the new index steals a query
another status index already serves.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# Minimal stand-in for the real table: only the columns the hot queries touch.
# The UNIQUE constraint mirrors the schema and creates an implicit index that
# leads with message_id but cannot provide timestamp order.
TABLE_DDL = (
    "CREATE TABLE msg_message_status ("
    "id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
    "status TEXT NOT NULL, timestamp INTEGER NOT NULL, UNIQUE(message_id, user_id))"
)

# The "before" state: the exact indexes the messaging schema defines for this
# table. This is what an already-migrated deployment runs with.
BASELINE_INDEX_DDL = (
    "CREATE INDEX idx_msg_status_message ON msg_message_status(message_id)",
    "CREATE INDEX idx_msg_status_user ON msg_message_status(user_id)",
)

# The status detail panel: every receipt for one message, newest-first.
RECEIPT_LIST_QUERY = (
    "SELECT * FROM msg_message_status WHERE message_id = ? ORDER BY timestamp"
)
RECEIPT_LIST_PARAMS = (42,)

# The reader-avatar path: only readers of one message, oldest-first.
READER_IDS_QUERY = (
    "SELECT user_id FROM msg_message_status "
    "WHERE message_id = ? AND status = 'read' ORDER BY timestamp ASC"
)
READER_IDS_PARAMS = (42,)

# A different hot filter must keep its own index; the new leading-message_id
# index must not steal it.
USER_FILTER_QUERY = (
    "SELECT * FROM msg_message_status WHERE user_id = ? ORDER BY timestamp ASC"
)
USER_FILTER_PARAMS = (7,)


def _load_migration():
    path = Path(pkg.__file__).parent / "013_message_status_receipt_index.py"
    spec = importlib.util.spec_from_file_location(
        "migration_013_message_status_index", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Db:
    """Minimal db facade: the migration only calls ``execute``."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        return self.conn.execute(sql)


def _make_db(with_indexes):
    conn = sqlite3.connect(":memory:")
    conn.execute(TABLE_DDL)
    for ddl in BASELINE_INDEX_DDL:
        conn.execute(ddl)
    if with_indexes:
        _load_migration().up(_Db(conn))
    return conn


@pytest.fixture
def indexed_db():
    conn = _make_db(with_indexes=True)
    yield conn
    conn.close()


def _plan(conn, query, params):
    return "\n".join(
        row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query, params)
    )


def _seed(conn):
    """Insert receipts with interleaved sort keys across messages and users."""
    conn.executemany(
        "INSERT INTO msg_message_status (message_id, user_id, status, timestamp) "
        "VALUES (?, ?, ?, ?)",
        [
            (42, 7, "read", 500),
            (42, 8, "delivered", 100),
            (42, 9, "read", 300),
            (42, 10, "read", 200),
            (43, 7, "read", 400),
            (43, 11, "delivered", 150),
        ],
    )
    conn.execute("ANALYZE")
    conn.commit()


class TestMessageStatusReceiptIndex:
    def test_index_definitions_are_static_identifiers(self):
        migration = _load_migration()
        covered = {(name, table, cols) for name, table, cols in migration.INDEXES}
        assert len(covered) == len(migration.INDEXES)
        for name, table, columns in migration.INDEXES:
            require_identifier(name, "index name")
            require_identifier(table, "table name")
            assert columns, f"{name} must define at least one column"
            for col in columns:
                require_identifier(col, "column name")

    def test_index_matches_schema_definition(self):
        # The migration that retrofits existing databases must not drift from
        # the authoritative schema definition used for fresh installs.
        from src.core.messaging.schema import SCHEMA_SQLITE

        migration = _load_migration()
        for name, table, columns in migration.INDEXES:
            definition = f"{table}({', '.join(columns)})"
            assert f"CREATE INDEX IF NOT EXISTS {name} ON {definition}" in SCHEMA_SQLITE

    def test_up_is_idempotent(self, indexed_db):
        # CREATE INDEX IF NOT EXISTS must let a re-run (or a fresh install that
        # already created the index via the schema) pass without error.
        _load_migration().up(_Db(indexed_db))

    @pytest.mark.parametrize(
        "query,params",
        [
            (RECEIPT_LIST_QUERY, RECEIPT_LIST_PARAMS),
            (READER_IDS_QUERY, READER_IDS_PARAMS),
        ],
    )
    def test_plan_uses_composite_index(self, indexed_db, query, params):
        _seed(indexed_db)
        plan = _plan(indexed_db, query, params)
        assert "idx_msg_status_message_timestamp" in plan, f"plan:\n{plan}"
        assert "SCAN " not in plan, f"hot query regressed to a full scan:\n{plan}"
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan, (
            f"hot query regressed to a temporary sort:\n{plan}"
        )

    @pytest.mark.parametrize(
        "query,params",
        [
            (RECEIPT_LIST_QUERY, RECEIPT_LIST_PARAMS),
            (READER_IDS_QUERY, READER_IDS_PARAMS),
        ],
    )
    def test_baseline_sorts_without_composite_index(self, query, params):
        # Before migration 013 the single-column message_id index finds the
        # rows but the planner materialises a temporary B-tree to order them.
        conn = _make_db(with_indexes=False)
        try:
            _seed(conn)
            plan = _plan(conn, query, params)
            assert "USE TEMP B-TREE FOR ORDER BY" in plan, f"plan:\n{plan}"
            assert "idx_msg_status_message_timestamp" not in plan
        finally:
            conn.close()

    def test_other_filter_keeps_its_index(self, indexed_db):
        _seed(indexed_db)
        plan = _plan(indexed_db, USER_FILTER_QUERY, USER_FILTER_PARAMS)
        assert "idx_msg_status_user" in plan, f"expected idx_msg_status_user:\n{plan}"
        assert "idx_msg_status_message_timestamp" not in plan, (
            f"new index stole a query served by idx_msg_status_user:\n{plan}"
        )

    @pytest.mark.parametrize(
        "query,params",
        [
            (RECEIPT_LIST_QUERY, RECEIPT_LIST_PARAMS),
            (READER_IDS_QUERY, READER_IDS_PARAMS),
        ],
    )
    def test_results_parity(self, query, params):
        # Adding an index must not change which rows come back or their order.
        baseline = _make_db(with_indexes=False)
        indexed = _make_db(with_indexes=True)
        try:
            _seed(baseline)
            _seed(indexed)
            before = [tuple(r) for r in baseline.execute(query, params)]
            after = [tuple(r) for r in indexed.execute(query, params)]
            assert before == after
            assert before, "parity check needs at least one row to be meaningful"
        finally:
            baseline.close()
            indexed.close()
