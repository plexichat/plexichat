"""Query-plan and result-parity guards for the admin audit action index (011).

An index is only useful if the planner actually picks it. These tests apply the
real migration to an in-memory SQLite database and read ``EXPLAIN QUERY PLAN``
for the hot cross-worker tuning audit read it is meant to serve. They fail when
the index disappears, when the plan regresses to a temporary sort B-tree, when
adding the index changes the rows a query returns, or when the new index steals
a query another audit index already serves optimally.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# Minimal stand-ins for the two real tables: only the columns the hot query
# touches. Keeping the DDL here makes the plan deterministic and independent of
# the rest of the schema.
TABLE_DDL = (
    "CREATE TABLE admin_audit_log (id INTEGER PRIMARY KEY, admin_id INTEGER NOT NULL, "
    "action TEXT NOT NULL, target_type TEXT, target_id INTEGER, target_user_id INTEGER, "
    "details TEXT, ip_address TEXT, user_agent TEXT, "
    "status TEXT NOT NULL DEFAULT 'success', created_at INTEGER NOT NULL)",
    "CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL)",
)

# The "before" state: the exact single-column indexes the admin schema defines
# for admin_audit_log. This is what an already-migrated deployment runs with.
BASELINE_INDEX_DDL = (
    "CREATE INDEX idx_admin_audit_admin ON admin_audit_log(admin_id)",
    "CREATE INDEX idx_admin_audit_admin_created ON admin_audit_log(admin_id, created_at)",
    "CREATE INDEX idx_admin_audit_action ON admin_audit_log(action)",
    "CREATE INDEX idx_admin_audit_target ON admin_audit_log(target_type, target_id)",
    "CREATE INDEX idx_admin_audit_created ON admin_audit_log(created_at)",
    "CREATE INDEX idx_admin_audit_status ON admin_audit_log(status)",
)

# The hot read from GET /dashboard/cross-worker-tuning/audit: an exact action
# filter, newest-first, with a small LIMIT.
ACTION_FEED_QUERY = (
    "SELECT a.id, a.admin_id, u.username, a.details, a.ip_address, a.status, "
    "a.created_at FROM admin_audit_log a "
    "LEFT JOIN admin_users u ON a.admin_id = u.id "
    "WHERE a.action = ? ORDER BY a.created_at DESC LIMIT ?"
)
ACTION_FEED_PARAMS = ("cross_worker_tuning_update", 20)

# Other hot audit filters must keep their own indexes; the new leading-action
# index must not steal them.
STATUS_FILTER_QUERY = (
    "SELECT id FROM admin_audit_log WHERE status = ? ORDER BY created_at DESC LIMIT ?"
)
STATUS_FILTER_PARAMS = ("success", 20)
ADMIN_FILTER_QUERY = (
    "SELECT id FROM admin_audit_log WHERE admin_id = ? ORDER BY created_at DESC LIMIT ?"
)
ADMIN_FILTER_PARAMS = (7, 20)


def _load_migration():
    path = Path(pkg.__file__).parent / "011_admin_audit_action_created_index.py"
    spec = importlib.util.spec_from_file_location(
        "migration_011_admin_audit_index", path
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
    """Insert rows with interleaved sort keys across two actions/admins."""
    conn.executemany(
        "INSERT INTO admin_audit_log "
        "(id, admin_id, action, status, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            (1, 7, "cross_worker_tuning_update", "success", 300),
            (2, 7, "login", "success", 100),
            (3, 8, "cross_worker_tuning_update", "failed", 200),
            (4, 7, "cross_worker_tuning_update", "success", 50),
            (5, 8, "user_update", "success", 400),
        ],
    )
    conn.executemany(
        "INSERT INTO admin_users (id, username) VALUES (?, ?)",
        [(7, "alice"), (8, "bob")],
    )
    conn.commit()


class TestAdminAuditActionIndex:
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
        from src.core.admin.schema import SCHEMA

        migration = _load_migration()
        for name, table, columns in migration.INDEXES:
            definition = f"{table}({', '.join(columns)})"
            assert f"CREATE INDEX IF NOT EXISTS {name} ON {definition}" in SCHEMA

    def test_up_is_idempotent(self, indexed_db):
        # CREATE INDEX IF NOT EXISTS must let a re-run (or a fresh install that
        # already created the index via the schema) pass without error.
        _load_migration().up(_Db(indexed_db))

    def test_plan_uses_composite_index(self, indexed_db):
        plan = _plan(indexed_db, ACTION_FEED_QUERY, ACTION_FEED_PARAMS)
        assert "idx_admin_audit_action_created" in plan, f"plan:\n{plan}"
        assert "SCAN " not in plan, f"hot query regressed to a full scan:\n{plan}"
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan, (
            f"hot query regressed to a temporary sort:\n{plan}"
        )

    def test_baseline_sorts_without_composite_index(self):
        # Before migration 011 the single-column action index finds the rows
        # but the planner materialises a temporary B-tree to order them.
        conn = _make_db(with_indexes=False)
        try:
            plan = _plan(conn, ACTION_FEED_QUERY, ACTION_FEED_PARAMS)
            assert "USE TEMP B-TREE FOR ORDER BY" in plan, f"plan:\n{plan}"
            assert "idx_admin_audit_action_created" not in plan
        finally:
            conn.close()

    @pytest.mark.parametrize(
        "query,params,index_name",
        [
            (STATUS_FILTER_QUERY, STATUS_FILTER_PARAMS, "idx_admin_audit_status"),
            (ADMIN_FILTER_QUERY, ADMIN_FILTER_PARAMS, "idx_admin_audit_admin_created"),
        ],
    )
    def test_other_filters_keep_their_index(
        self, indexed_db, query, params, index_name
    ):
        plan = _plan(indexed_db, query, params)
        assert index_name in plan, f"expected {index_name} in plan:\n{plan}"
        assert "idx_admin_audit_action_created" not in plan, (
            f"new index stole a query served by {index_name}:\n{plan}"
        )

    def test_results_parity(self):
        # Adding an index must not change which rows come back or their order.
        baseline = _make_db(with_indexes=False)
        indexed = _make_db(with_indexes=True)
        try:
            _seed(baseline)
            _seed(indexed)
            before = [
                tuple(r)
                for r in baseline.execute(ACTION_FEED_QUERY, ACTION_FEED_PARAMS)
            ]
            after = [
                tuple(r) for r in indexed.execute(ACTION_FEED_QUERY, ACTION_FEED_PARAMS)
            ]
            assert before == after
            assert before, "parity check needs at least one row to be meaningful"
        finally:
            baseline.close()
            indexed.close()
