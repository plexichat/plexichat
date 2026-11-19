"""Query-plan and result-parity guards for the server lookup indexes (migration 010).

An index is only useful if the planner actually picks it. These tests apply the
real migration to an in-memory SQLite database and read ``EXPLAIN QUERY PLAN``
for the hot server reads it is meant to serve. They fail when an index
disappears, when a plan regresses to a full scan or a temporary sort B-tree,
when adding the index changes the rows a query returns, or when the new index
steals a query that migration 007's composite index already serves optimally.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# Minimal stand-ins for the two real server tables: only the columns the hot
# queries touch. Keeping the DDL here makes the plan deterministic and
# independent of the rest of the schema.
TABLE_DDL = (
    "CREATE TABLE srv_scheduled_events (id INTEGER PRIMARY KEY, "
    "server_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled', "
    "start_time INTEGER NOT NULL)",
    "CREATE TABLE srv_event_rsvps (id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, "
    "user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'interested', "
    "created_at INTEGER NOT NULL)",
)

# The "before" state: the single-column indexes plus migration 007's composite,
# which is the exact schema an already-migrated deployment runs with.
BASELINE_INDEX_DDL = (
    "CREATE INDEX idx_srv_events_server ON srv_scheduled_events(server_id)",
    "CREATE INDEX idx_srv_events_status ON srv_scheduled_events(status)",
    "CREATE INDEX idx_srv_events_start ON srv_scheduled_events(start_time)",
    "CREATE INDEX idx_srv_events_server_status_start "
    "ON srv_scheduled_events(server_id, status, start_time)",
    "CREATE INDEX idx_srv_rsvps_event ON srv_event_rsvps(event_id)",
    "CREATE INDEX idx_srv_rsvps_user ON srv_event_rsvps(user_id)",
)

# Each composite index paired with the hot queries it must serve. The set of
# keys is asserted equal to the migration's INDEXES so a new index cannot ship
# without a plan assertion. Cases carry concrete params so the same query can
# be used for both EXPLAIN and result-parity checks.
VERIFY_QUERIES = {
    "idx_srv_events_server_start": {
        "events_all_by_start": (
            "SELECT id FROM srv_scheduled_events "
            "WHERE server_id = ? ORDER BY start_time ASC LIMIT ?",
            (1, 5),
        ),
    },
    "idx_srv_event_rsvps_event_created": {
        "rsvps_oldest_first": (
            "SELECT id FROM srv_event_rsvps "
            "WHERE event_id = ? ORDER BY created_at ASC LIMIT ?",
            (1, 5),
        ),
        "rsvps_going_oldest_first": (
            "SELECT id FROM srv_event_rsvps "
            "WHERE event_id = ? AND status = 'going' "
            "ORDER BY created_at ASC LIMIT ?",
            (1, 5),
        ),
    },
}

# Migration 007's status-filtered event query must keep using its composite
# index; the new leading-prefix index must not steal it and reintroduce a sort.
STATUS_EVENT_QUERY = (
    "SELECT id FROM srv_scheduled_events "
    "WHERE server_id = ? AND status = 'scheduled' "
    "ORDER BY start_time ASC LIMIT ?"
)
STATUS_EVENT_PARAMS = (1, 5)


def _load_migration():
    path = Path(pkg.__file__).parent / "010_server_hot_lookup_indexes.py"
    spec = importlib.util.spec_from_file_location("migration_010_server", path)
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
    conn.row_factory = sqlite3.Row
    for ddl in TABLE_DDL:
        conn.execute(ddl)
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
    """Insert rows with interleaved sort keys across two parent groups."""
    conn.executemany(
        "INSERT INTO srv_scheduled_events (id, server_id, status, start_time) "
        "VALUES (?, ?, ?, ?)",
        [
            (1, 1, "scheduled", 300),
            (2, 1, "scheduled", 100),
            (3, 2, "scheduled", 200),
            (4, 1, "ongoing", 50),
            (5, 1, "scheduled", 400),
        ],
    )
    conn.executemany(
        "INSERT INTO srv_event_rsvps (id, event_id, user_id, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, 10, "interested", 300),
            (2, 1, 11, "going", 100),
            (3, 2, 12, "going", 200),
            (4, 1, 13, "going", 50),
            (5, 1, 14, "interested", 400),
        ],
    )
    conn.commit()


_ALL_CASES = [
    (index_name, case_name, sql, params)
    for index_name, cases in VERIFY_QUERIES.items()
    for case_name, (sql, params) in cases.items()
]


class TestServerHotLookupIndexes:
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
        from src.core.servers.schema import SCHEMA

        migration = _load_migration()
        for name, table, columns in migration.INDEXES:
            definition = f"{table}({', '.join(columns)})"
            assert f"CREATE INDEX IF NOT EXISTS {name} ON {definition}" in SCHEMA

    def test_every_index_has_a_verification_query(self):
        migration = _load_migration()
        assert {name for name, _, _ in migration.INDEXES} == set(VERIFY_QUERIES)

    def test_up_is_idempotent(self, indexed_db):
        # CREATE INDEX IF NOT EXISTS must let a re-run (or a fresh install that
        # already created the index via the schema) pass without error.
        _load_migration().up(_Db(indexed_db))

    @pytest.mark.parametrize(
        "index_name,case_name,sql,params",
        _ALL_CASES,
        ids=[f"{i}-{c}" for i, c, _, _ in _ALL_CASES],
    )
    def test_plan_uses_composite_index(
        self, indexed_db, index_name, case_name, sql, params
    ):
        plan = _plan(indexed_db, sql, params)
        assert index_name in plan, f"expected {index_name} in plan:\n{plan}"
        assert "SCAN " not in plan, f"hot query regressed to a full scan:\n{plan}"
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan, (
            f"hot query regressed to a temporary sort:\n{plan}"
        )

    def test_status_event_query_still_uses_007_index(self, indexed_db):
        # Adding the new leading-prefix index must not steal the status-
        # filtered query that migration 007's composite index already serves.
        plan = _plan(indexed_db, STATUS_EVENT_QUERY, STATUS_EVENT_PARAMS)
        assert "idx_srv_events_server_status_start" in plan
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan

    @pytest.mark.parametrize(
        "index_name,case_name,sql,params",
        _ALL_CASES,
        ids=[f"{i}-{c}-baseline" for i, c, _, _ in _ALL_CASES],
    )
    def test_baseline_sort_sorts_without_the_composite_index(
        self, index_name, case_name, sql, params
    ):
        # Before migration 010 the single-column index (or migration 007's
        # composite with a free status column) finds the rows but the planner
        # materialises a temporary B-tree to order them.
        conn = _make_db(with_indexes=False)
        try:
            plan = _plan(conn, sql, params)
            assert "USE TEMP B-TREE FOR ORDER BY" in plan
            assert index_name not in plan
        finally:
            conn.close()

    @pytest.mark.parametrize(
        "index_name,case_name,sql,params",
        _ALL_CASES,
        ids=[f"{i}-{c}" for i, c, _, _ in _ALL_CASES],
    )
    def test_results_parity(self, index_name, case_name, sql, params):
        # Adding an index must not change which rows come back or their order.
        baseline = _make_db(with_indexes=False)
        indexed = _make_db(with_indexes=True)
        try:
            _seed(baseline)
            _seed(indexed)
            before = [tuple(r) for r in baseline.execute(sql, params)]
            after = [tuple(r) for r in indexed.execute(sql, params)]
            assert before == after
            assert before, "parity check needs at least one row to be meaningful"
        finally:
            baseline.close()
            indexed.close()
