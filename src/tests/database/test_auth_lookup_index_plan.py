"""Query-plan and result-parity guards for the auth lookup indexes (migration 008).

An index is only useful if the planner actually picks it. These tests apply the
real migration to an in-memory SQLite database and read ``EXPLAIN QUERY PLAN``
for the hot auth reads it is meant to serve. They fail when an index disappears,
when a plan regresses to a full scan or a temporary sort B-tree, or when adding
the index changes the rows a query returns.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# Minimal stand-ins for the two real auth tables: only the columns the hot
# queries touch. Keeping the DDL here makes the plan deterministic and
# independent of the rest of the schema.
TABLE_DDL = (
    "CREATE TABLE auth_email_tokens (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
    "token_hash TEXT NOT NULL, token_type TEXT NOT NULL, expires_at INTEGER NOT NULL, "
    "used INTEGER DEFAULT 0, created_at INTEGER NOT NULL)",
    "CREATE TABLE auth_audit_log (id INTEGER PRIMARY KEY, user_id INTEGER, "
    "event_type TEXT NOT NULL, ip_index TEXT, ip_encrypted TEXT, device_id INTEGER, "
    "timestamp INTEGER NOT NULL, details_encrypted TEXT, success INTEGER NOT NULL)",
)

# The "before" state: no index on auth_email_tokens, and only the single-column
# user index on auth_audit_log.
BASELINE_INDEX_DDL = ("CREATE INDEX idx_auth_audit_user ON auth_audit_log(user_id)",)

# Each composite index paired with the hot queries it must serve. The set of
# keys is asserted equal to the migration's INDEXES so a new index cannot ship
# without a plan assertion. Cases carry concrete params so the same query can
# be used for both EXPLAIN and result-parity checks.
VERIFY_QUERIES = {
    "idx_auth_email_tokens_user_type_created": {
        "reset_throttle": (
            "SELECT COUNT(*) AS cnt FROM auth_email_tokens "
            "WHERE user_id = ? AND token_type = ? AND created_at > ?",
            (1, "reset_password", 100),
        ),
        "newest_token": (
            "SELECT created_at, expires_at FROM auth_email_tokens "
            "WHERE user_id = ? AND token_type = ? ORDER BY created_at DESC",
            (1, "reset_password"),
        ),
    },
    "idx_auth_audit_user_timestamp": {
        "user_feed": (
            "SELECT id, event_type, timestamp FROM auth_audit_log "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (1, 5),
        ),
        "user_event_feed": (
            "SELECT id, event_type, timestamp FROM auth_audit_log "
            "WHERE user_id = ? AND event_type IN (?, ?, ?) "
            "ORDER BY timestamp DESC LIMIT ?",
            (1, "login", "logout", "reset", 5),
        ),
    },
}


def _load_migration():
    path = Path(pkg.__file__).parent / "008_auth_lookup_indexes.py"
    spec = importlib.util.spec_from_file_location("migration_008_auth", path)
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
    """Insert rows for two users / token types with deliberately interleaved times."""
    conn.executemany(
        "INSERT INTO auth_email_tokens "
        "(id, user_id, token_hash, token_type, expires_at, used, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?)",
        [
            (1, 1, "h1", "reset_password", 200, 10),
            (2, 1, "h2", "reset_password", 300, 110),
            (3, 1, "h3", "verify_email", 400, 60),
            (4, 2, "h4", "reset_password", 500, 160),
            (5, 1, "h5", "reset_password", 100, 160),  # boundary: created_at == 100
        ],
    )
    conn.executemany(
        "INSERT INTO auth_audit_log "
        "(id, user_id, event_type, timestamp, success) VALUES (?, ?, ?, ?, 1)",
        [
            (1, 1, "login", 10),
            (2, 1, "logout", 30),
            (3, 2, "login", 20),
            (4, 1, "reset", 50),
            (5, 1, "login", 40),
        ],
    )
    conn.commit()


_ALL_CASES = [
    (index_name, case_name, sql, params)
    for index_name, cases in VERIFY_QUERIES.items()
    for case_name, (sql, params) in cases.items()
]


class TestAuthLookupIndexes:
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
        from src.core.auth.schema import SCHEMA_SQLITE

        migration = _load_migration()
        for name, table, columns in migration.INDEXES:
            definition = f"{table}({', '.join(columns)})"
            assert f"CREATE INDEX IF NOT EXISTS {name}\n    ON {definition}" in (
                SCHEMA_SQLITE
            ) or f"CREATE INDEX IF NOT EXISTS {name} ON {definition}" in (SCHEMA_SQLITE)

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

    def test_baseline_email_token_lookup_scans_the_table(self):
        # Before migration 008 there is no index at all on auth_email_tokens,
        # so the reset throttle scans every token ever issued.
        conn = _make_db(with_indexes=False)
        try:
            sql, params = VERIFY_QUERIES["idx_auth_email_tokens_user_type_created"][
                "reset_throttle"
            ]
            plan = _plan(conn, sql, params)
            assert "SCAN auth_email_tokens" in plan
            assert "idx_auth_email_tokens_user_type_created" not in plan
        finally:
            conn.close()

    def test_baseline_audit_feed_sorts_without_the_composite_index(self):
        # Before migration 008 the single-column user index finds the rows but
        # the planner materialises a temporary B-tree to order them.
        conn = _make_db(with_indexes=False)
        try:
            sql, params = VERIFY_QUERIES["idx_auth_audit_user_timestamp"]["user_feed"]
            plan = _plan(conn, sql, params)
            assert "USE TEMP B-TREE FOR ORDER BY" in plan
            assert "idx_auth_audit_user_timestamp" not in plan
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
