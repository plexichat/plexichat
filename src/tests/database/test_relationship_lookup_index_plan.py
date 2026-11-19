"""Query-plan and result-parity guards for the relationship lookup indexes (migration 009).

An index is only useful if the planner actually picks it. These tests apply the
real migration to an in-memory SQLite database and read ``EXPLAIN QUERY PLAN``
for the hot relationship reads it is meant to serve. They fail when an index
disappears, when a plan regresses to a full scan or a temporary sort B-tree, or
when adding the index changes the rows a query returns.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# Minimal stand-ins for the two real relationship tables: only the columns the
# hot queries touch. Keeping the DDL here makes the plan deterministic and
# independent of the rest of the schema.
TABLE_DDL = (
    "CREATE TABLE rel_friends (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
    "friend_id INTEGER NOT NULL, created_at INTEGER NOT NULL, "
    "UNIQUE(user_id, friend_id))",
    "CREATE TABLE rel_friend_requests (id INTEGER PRIMARY KEY, sender_id INTEGER NOT NULL, "
    "recipient_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
    "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
    "UNIQUE(sender_id, recipient_id))",
)

# The "before" state: only the single-column indexes the schema had before the
# composite ones were added.
BASELINE_INDEX_DDL = (
    "CREATE INDEX idx_rel_friends_user ON rel_friends(user_id)",
    "CREATE INDEX idx_rel_friends_friend ON rel_friends(friend_id)",
    "CREATE INDEX idx_rel_requests_sender ON rel_friend_requests(sender_id)",
    "CREATE INDEX idx_rel_requests_recipient ON rel_friend_requests(recipient_id)",
)

# Each composite index paired with the hot queries it must serve. The set of
# keys is asserted equal to the migration's INDEXES so a new index cannot ship
# without a plan assertion. Cases carry concrete params so the same query can
# be used for both EXPLAIN and result-parity checks.
VERIFY_QUERIES = {
    "idx_rel_friends_user_created": {
        "friends_newest": (
            "SELECT friend_id FROM rel_friends "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (1, 5),
        ),
    },
    "idx_rel_requests_recipient_status_created": {
        "pending_incoming": (
            "SELECT id FROM rel_friend_requests "
            "WHERE recipient_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT ?",
            (1, 5),
        ),
    },
    "idx_rel_requests_sender_status_created": {
        "pending_outgoing": (
            "SELECT id FROM rel_friend_requests "
            "WHERE sender_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT ?",
            (1, 5),
        ),
    },
}


def _load_migration():
    path = Path(pkg.__file__).parent / "009_relationship_lookup_indexes.py"
    spec = importlib.util.spec_from_file_location("migration_009_relationships", path)
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
    """Insert rows for two users with deliberately interleaved times."""
    conn.executemany(
        "INSERT INTO rel_friends (id, user_id, friend_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        [
            (1, 1, 10, 100),
            (2, 1, 11, 300),
            (3, 2, 12, 200),
            (4, 1, 13, 200),
            (5, 1, 14, 50),
        ],
    )
    conn.executemany(
        "INSERT INTO rel_friend_requests "
        "(id, sender_id, recipient_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 10, 1, "pending", 100, 100),
            (2, 11, 1, "pending", 300, 300),
            (3, 12, 2, "pending", 200, 200),
            (4, 1, 10, "pending", 150, 150),
            (5, 13, 1, "declined", 400, 400),
            (6, 1, 11, "pending", 250, 250),
        ],
    )
    conn.commit()


_ALL_CASES = [
    (index_name, case_name, sql, params)
    for index_name, cases in VERIFY_QUERIES.items()
    for case_name, (sql, params) in cases.items()
]


class TestRelationshipLookupIndexes:
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
        from src.core.relationships.schema import SCHEMA

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

    @pytest.mark.parametrize(
        "index_name,case_name,sql,params",
        _ALL_CASES,
        ids=[f"{i}-{c}-baseline" for i, c, _, _ in _ALL_CASES],
    )
    def test_baseline_sort_sorts_without_the_composite_index(
        self, index_name, case_name, sql, params
    ):
        # Before migration 009 the single-column index finds the rows but the
        # planner materialises a temporary B-tree to order them. This is the
        # exact regression the assertions above guard against.
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
