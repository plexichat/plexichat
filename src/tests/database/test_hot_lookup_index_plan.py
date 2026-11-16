"""Verification query-plan test for the composite lookup indexes (migration 007).

A migration that adds an index is only useful if the planner actually picks it.
These tests apply the real migration to an in-memory SQLite database and read
``EXPLAIN QUERY PLAN`` for a representative hot query per index. They fail both
when an index disappears and when the plan regresses to a full scan or a
temporary sort B-tree.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# Minimal stand-ins for the three real tables: only the columns the hot queries
# touch. Keeping the DDL here makes the plan deterministic and independent of
# the rest of the schema.
TABLE_DDL = (
    "CREATE TABLE srv_audit_log (id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL, "
    "user_id INTEGER NOT NULL, action TEXT NOT NULL, created_at INTEGER NOT NULL)",
    "CREATE TABLE msg_messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, "
    "author_id INTEGER, created_at INTEGER)",
    "CREATE TABLE srv_scheduled_events (id INTEGER PRIMARY KEY, server_id INTEGER, "
    "status TEXT, start_time INTEGER)",
)

# The single-column indexes the schema has today -- the "before" state.
BASELINE_INDEX_DDL = (
    "CREATE INDEX idx_srv_audit_server ON srv_audit_log(server_id)",
    "CREATE INDEX idx_msg_messages_author ON msg_messages(author_id)",
    "CREATE INDEX idx_srv_events_server ON srv_scheduled_events(server_id)",
)

# Each composite index paired with the hot query it must serve. The set of keys
# is asserted equal to the migration's INDEXES so a new index cannot silently
# ship without a plan assertion.
VERIFY_QUERIES = {
    "idx_srv_audit_server_created": (
        "SELECT * FROM srv_audit_log WHERE server_id = 1 "
        "ORDER BY created_at DESC LIMIT 50"
    ),
    "idx_msg_messages_author_created": (
        "SELECT id FROM msg_messages WHERE author_id = 1 "
        "ORDER BY created_at DESC LIMIT 50"
    ),
    "idx_srv_events_server_status_start": (
        "SELECT id FROM srv_scheduled_events "
        "WHERE server_id = 1 AND status = 'scheduled' "
        "ORDER BY start_time LIMIT 20"
    ),
}


def _load_migration():
    path = Path(pkg.__file__).parent / "007_hot_lookup_composite_indexes.py"
    spec = importlib.util.spec_from_file_location("migration_007", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Db:
    """Minimal db facade: the migration only calls ``execute``."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        return self.conn.execute(sql)


@pytest.fixture
def indexed_db():
    conn = sqlite3.connect(":memory:")
    for ddl in TABLE_DDL:
        conn.execute(ddl)
    _load_migration().up(_Db(conn))
    yield conn
    conn.close()


def _plan(conn, query):
    return "\n".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query))


class TestHotLookupIndexes:
    def test_index_definitions_are_static_identifiers(self):
        migration = _load_migration()
        covered = {(cols, name) for name, cols in migration.INDEXES}
        assert len(covered) == len(migration.INDEXES)
        for name, columns in migration.INDEXES:
            require_identifier(name, "index name")
            table, cols = columns.split("(", 1)
            require_identifier(table, "table name")
            for col in cols.rstrip(")").split(","):
                require_identifier(col.strip(), "column name")

    def test_every_index_has_a_verification_query(self):
        migration = _load_migration()
        assert {name for name, _ in migration.INDEXES} == set(VERIFY_QUERIES)

    @pytest.mark.parametrize("index_name,query", sorted(VERIFY_QUERIES.items()))
    def test_plan_uses_composite_index(self, indexed_db, index_name, query):
        plan = _plan(indexed_db, query)
        assert index_name in plan, f"expected {index_name} in plan:\n{plan}"
        assert "SCAN " not in plan, f"hot query regressed to a full scan:\n{plan}"
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan, (
            f"hot query regressed to a temporary sort:\n{plan}"
        )

    def test_baseline_plan_sorts_without_the_composite_index(self):
        # Before migration 007 the single-column index finds the rows but the
        # planner has to materialise a temporary B-tree to order them. This is
        # the exact regression the assertions above guard against.
        conn = sqlite3.connect(":memory:")
        try:
            for ddl in TABLE_DDL:
                conn.execute(ddl)
            for ddl in BASELINE_INDEX_DDL:
                conn.execute(ddl)
            plan = _plan(conn, VERIFY_QUERIES["idx_srv_audit_server_created"])
            assert "USE TEMP B-TREE FOR ORDER BY" in plan
            assert "idx_srv_audit_server_created" not in plan
        finally:
            conn.close()
