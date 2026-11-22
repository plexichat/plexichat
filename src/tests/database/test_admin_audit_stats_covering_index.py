"""Query-plan and parity guards for the admin-audit stats covering index (012).

``GET /api/v1/admin/audit/logs/stats`` filters a UTC-midnight ``created_at``
range eight times by ``action LIKE '<family>%'``. With the single-column indexes
the planner seeks ``idx_admin_audit_created`` and then fetches every candidate
row from the table just to evaluate the LIKE. Migration 012 adds a composite
``(created_at, status, action)`` index that answers those counts index-only.

These tests apply the real migration to an in-memory SQLite database and read
``EXPLAIN QUERY PLAN`` for each stats query. They fail when the index is absent,
when a family count regresses to a non-covering seek/table scan, when the new
index steals a filter another audit index already serves, or when adding it
changes the counts.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# All production admin_audit_log indexes, including those added by migration
# 011, i.e. the state of an already-migrated deployment before migration 012.
TABLE_DDL = (
    "CREATE TABLE admin_audit_log (id INTEGER PRIMARY KEY, admin_id INTEGER NOT NULL, "
    "action TEXT NOT NULL, target_type TEXT, target_id INTEGER, target_user_id INTEGER, "
    "details TEXT, ip_address TEXT, user_agent TEXT, "
    "status TEXT NOT NULL DEFAULT 'success', created_at INTEGER NOT NULL)",
)
BASELINE_INDEX_DDL = (
    "CREATE INDEX idx_admin_audit_admin ON admin_audit_log(admin_id)",
    "CREATE INDEX idx_admin_audit_admin_created ON admin_audit_log(admin_id, created_at)",
    "CREATE INDEX idx_admin_audit_action ON admin_audit_log(action)",
    "CREATE INDEX idx_admin_audit_action_created ON admin_audit_log(action, created_at)",
    "CREATE INDEX idx_admin_audit_target ON admin_audit_log(target_type, target_id)",
    "CREATE INDEX idx_admin_audit_created ON admin_audit_log(created_at)",
    "CREATE INDEX idx_admin_audit_status ON admin_audit_log(status)",
)

INDEX_NAME = "idx_admin_audit_created_status_action"
TODAY = 1_760_000_000_000
FAMILIES = (
    "login",
    "user",
    "admin",
    "config",
    "approval",
    "security",
    "plexijoin",
    "license",
)

# The exact shapes the stats route issues. The family query is the hot one: the
# endpoint runs eight variants of it on every request.
FAMILY_COUNT_QUERY = (
    "SELECT COUNT(*) as count FROM admin_audit_log "
    "WHERE created_at >= ? AND action LIKE ?"
)
STATUS_COUNT_QUERY = (
    "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ? AND status = ?"
)
TOTAL_COUNT_QUERY = (
    "SELECT COUNT(*) as count FROM admin_audit_log WHERE created_at >= ?"
)
ADMIN_FILTER_QUERY = (
    "SELECT id FROM admin_audit_log WHERE admin_id = ? ORDER BY created_at DESC LIMIT ?"
)
ACTION_FEED_QUERY = (
    "SELECT id FROM admin_audit_log WHERE action = ? ORDER BY created_at DESC LIMIT ?"
)


def _load_migration():
    path = Path(pkg.__file__).parent / "012_admin_audit_stats_covering_index.py"
    spec = importlib.util.spec_from_file_location(
        "migration_012_admin_audit_stats_index", path
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


def _make_db(with_index):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (*TABLE_DDL, *BASELINE_INDEX_DDL):
        conn.execute(ddl)
    if with_index:
        _load_migration().up(_Db(conn))
    return conn


@pytest.fixture
def indexed_db():
    conn = _make_db(with_index=True)
    yield conn
    conn.close()


def _plan(conn, query, params):
    return "\n".join(
        row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query, params)
    )


def _seed(conn):
    """Seed enough rows with mixed families/statuses for stable planner choice."""
    rows = []
    for i in range(500):
        family = FAMILIES[i % len(FAMILIES)]
        status = "success" if i % 3 else "failed"
        rows.append(
            (
                i + 1,
                7 if i % 2 else 8,
                f"{family}.do",
                status,
                TODAY + i,
            )
        )
    conn.executemany(
        "INSERT INTO admin_audit_log (id, admin_id, action, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


class TestAdminAuditStatsCoveringIndex:
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
        _load_migration().up(_Db(indexed_db))

    def test_down_drops_index(self, indexed_db):
        migration = _load_migration()
        migration.down(_Db(indexed_db))
        names = {
            row[0]
            for row in indexed_db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert INDEX_NAME not in names

    def test_baseline_family_count_is_not_covering(self):
        conn = _make_db(with_index=False)
        try:
            _seed(conn)
            plan = _plan(conn, FAMILY_COUNT_QUERY, (TODAY, "login%"))
            assert INDEX_NAME not in plan, f"plan:\n{plan}"
            assert "idx_admin_audit_created" in plan, f"plan:\n{plan}"
            assert "COVERING INDEX" not in plan, f"plan:\n{plan}"
        finally:
            conn.close()

    @pytest.mark.parametrize("family", FAMILIES)
    def test_every_family_count_uses_covering_index(self, indexed_db, family):
        # All eight families in the stats loop must be served index-only; a
        # single non-covering family still means per-row table fetches.
        _seed(indexed_db)
        plan = _plan(indexed_db, FAMILY_COUNT_QUERY, (TODAY, f"{family}%"))
        assert f"COVERING INDEX {INDEX_NAME}" in plan, f"{family} plan:\n{plan}"
        assert "USE TEMP B-TREE" not in plan, f"{family} plan:\n{plan}"

    @pytest.mark.parametrize(
        "query,params,expected_index",
        [
            (STATUS_COUNT_QUERY, (TODAY, "success"), "idx_admin_audit_status"),
            (ADMIN_FILTER_QUERY, (7, 20), "idx_admin_audit_admin_created"),
            (ACTION_FEED_QUERY, ("login.do", 20), "idx_admin_audit_action_created"),
        ],
    )
    def test_other_filters_keep_their_index(
        self, indexed_db, query, params, expected_index
    ):
        _seed(indexed_db)
        plan = _plan(indexed_db, query, params)
        assert expected_index in plan, f"expected {expected_index} in plan:\n{plan}"
        assert INDEX_NAME not in plan, (
            f"new index stole a query served by {expected_index}:\n{plan}"
        )

    @pytest.mark.parametrize(
        "query,params",
        [
            (FAMILY_COUNT_QUERY, (TODAY, "login%")),
            (FAMILY_COUNT_QUERY, (TODAY, "user%")),
            (STATUS_COUNT_QUERY, (TODAY, "success")),
            (STATUS_COUNT_QUERY, (TODAY, "failed")),
            (TOTAL_COUNT_QUERY, (TODAY,)),
        ],
    )
    def test_results_parity(self, query, params):
        # Adding an index must not change any count the stats endpoint reports.
        baseline = _make_db(with_index=False)
        indexed = _make_db(with_index=True)
        try:
            _seed(baseline)
            _seed(indexed)
            before = baseline.execute(query, params).fetchone()[0]
            after = indexed.execute(query, params).fetchone()[0]
            assert before == after
            assert before > 0, "parity check needs matching rows to be meaningful"
        finally:
            baseline.close()
            indexed.close()
