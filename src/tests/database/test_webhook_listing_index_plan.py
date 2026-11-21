"""Query-plan and result-parity guards for the webhook listing indexes (014).

An index is only useful if the planner actually picks it. These tests apply the
real migration to an in-memory SQLite database and read ``EXPLAIN QUERY PLAN``
for the two hot webhook listing queries it is meant to serve. They fail when an
index disappears, when a plan regresses to a temporary sort B-tree, when adding
the index changes the rows a query returns, or when the new index steals a query
another index already serves.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import src.core.migrations.migrations as pkg
from src.core.security.hardening import require_identifier

# Minimal stand-in for the real table: only the columns the hot queries touch.
TABLE_DDL = (
    "CREATE TABLE webhook_webhooks ("
    "id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, "
    "server_id INTEGER NOT NULL, token_hash TEXT NOT NULL, "
    "created_at INTEGER NOT NULL)"
)

# The "before" state: the exact indexes the webhook schema defines for this
# table apart from the new composite ones. This is what an already-migrated
# deployment runs with.
BASELINE_INDEX_DDL = (
    "CREATE INDEX idx_webhook_channel ON webhook_webhooks(channel_id)",
    "CREATE INDEX idx_webhook_server ON webhook_webhooks(server_id)",
    "CREATE INDEX idx_webhook_token ON webhook_webhooks(token_hash)",
)

# The channel webhook settings panel: every webhook for one channel,
# oldest-first.
CHANNEL_LIST_QUERY = (
    "SELECT * FROM webhook_webhooks WHERE channel_id = ? ORDER BY created_at"
)
CHANNEL_LIST_PARAMS = (42,)

# The server webhook settings panel: every webhook for one server,
# oldest-first.
SERVER_LIST_QUERY = (
    "SELECT * FROM webhook_webhooks WHERE server_id = ? ORDER BY created_at"
)
SERVER_LIST_PARAMS = (7,)

# A different hot filter must keep its own index; the new leading-owner
# indexes must not steal it.
TOKEN_LOOKUP_QUERY = "SELECT * FROM webhook_webhooks WHERE token_hash = ?"
TOKEN_LOOKUP_PARAMS = ("tok9",)

INDEX_NAMES = ("idx_webhook_channel_created", "idx_webhook_server_created")


def _load_migration():
    path = Path(pkg.__file__).parent / "014_webhook_listing_indexes.py"
    spec = importlib.util.spec_from_file_location(
        "migration_014_webhook_listing_indexes", path
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
    """Insert webhooks with interleaved sort keys across channels and servers."""
    conn.executemany(
        "INSERT INTO webhook_webhooks "
        "(channel_id, server_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
        [
            (42, 7, "tok1", 500),
            (42, 7, "tok2", 100),
            (42, 8, "tok3", 300),
            (43, 7, "tok4", 200),
            (43, 8, "tok5", 400),
            (43, 8, "tok9", 150),
        ],
    )
    conn.execute("ANALYZE")
    conn.commit()


class TestWebhookListingIndexes:
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
        from src.core.webhooks.schema import SCHEMA

        migration = _load_migration()
        for name, table, columns in migration.INDEXES:
            definition = f"{table}({', '.join(columns)})"
            assert f"CREATE INDEX IF NOT EXISTS {name} ON {definition}" in SCHEMA

    def test_up_is_idempotent(self, indexed_db):
        # CREATE INDEX IF NOT EXISTS must let a re-run (or a fresh install that
        # already created the index via the schema) pass without error.
        _load_migration().up(_Db(indexed_db))

    @pytest.mark.parametrize(
        "query,params,index_name",
        [
            (CHANNEL_LIST_QUERY, CHANNEL_LIST_PARAMS, "idx_webhook_channel_created"),
            (SERVER_LIST_QUERY, SERVER_LIST_PARAMS, "idx_webhook_server_created"),
        ],
    )
    def test_plan_uses_composite_index(self, indexed_db, query, params, index_name):
        _seed(indexed_db)
        plan = _plan(indexed_db, query, params)
        assert index_name in plan, f"plan:\n{plan}"
        assert "SCAN " not in plan, f"hot query regressed to a full scan:\n{plan}"
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan, (
            f"hot query regressed to a temporary sort:\n{plan}"
        )

    @pytest.mark.parametrize(
        "query,params",
        [
            (CHANNEL_LIST_QUERY, CHANNEL_LIST_PARAMS),
            (SERVER_LIST_QUERY, SERVER_LIST_PARAMS),
        ],
    )
    def test_baseline_sorts_without_composite_index(self, query, params):
        # Before migration 014 the single-column owner index finds the rows but
        # the planner materialises a temporary B-tree to order them.
        conn = _make_db(with_indexes=False)
        try:
            _seed(conn)
            plan = _plan(conn, query, params)
            assert "USE TEMP B-TREE FOR ORDER BY" in plan, f"plan:\n{plan}"
            for index_name in INDEX_NAMES:
                assert index_name not in plan
        finally:
            conn.close()

    def test_other_filter_keeps_its_index(self, indexed_db):
        _seed(indexed_db)
        plan = _plan(indexed_db, TOKEN_LOOKUP_QUERY, TOKEN_LOOKUP_PARAMS)
        assert "idx_webhook_token" in plan, f"expected idx_webhook_token:\n{plan}"
        for index_name in INDEX_NAMES:
            assert index_name not in plan, (
                f"new index stole a query served by idx_webhook_token:\n{plan}"
            )

    @pytest.mark.parametrize(
        "query,params",
        [
            (CHANNEL_LIST_QUERY, CHANNEL_LIST_PARAMS),
            (SERVER_LIST_QUERY, SERVER_LIST_PARAMS),
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
