"""Regression: ``EXPLAIN ANALYZE <write>`` must invalidate the query-result cache.

``_QUERY_CACHE_READ_PREFIXES`` lists ``EXPLAIN`` as a guaranteed read-only
statement prefix. That is true for bare ``EXPLAIN`` (it only prints a plan), but
NOT for ``EXPLAIN ANALYZE``: PostgreSQL *executes* the target statement in order
to collect real row counts/timings. ``EXPLAIN ANALYZE UPDATE authz_roles ...``
therefore commits a write while the prefix check classified it as a read and
left the short-lived query cache untouched -- so a cached ``admin`` role verdict
kept surviving the demotion until its TTL expired.

pg8000 sends parameterless statements through PostgreSQL's simple-query
protocol, so this is reachable through ``db.execute()``. After the fix ``EXPLAIN``
is no longer an exempt prefix: anything we cannot cheaply prove read-only fails
closed and clears the cache. Over-invalidating for a rare ``EXPLAIN SELECT`` is
harmless; serving a pre-write verdict is not.
"""

import re

import pytest
import utils.config as config
from src.core.database.core import Database
from src.core.database.core.execution import _query_mutates_data

pytestmark = [pytest.mark.unit, pytest.mark.security]

DEMOTE = "UPDATE authz_roles SET role = 'user' WHERE user_id = 1"


@pytest.fixture
def db(tmp_path):
    Database.reset()
    config.setup(
        config_path=str(tmp_path / "config.yaml"),
        default_config={
            "database": {
                "type": "sqlite",
                "path": str(tmp_path / "coherence.db"),
            }
        },
    )
    Database.reset()
    instance = Database()
    instance.connect()
    instance.execute(
        "CREATE TABLE authz_roles (user_id INTEGER PRIMARY KEY, role TEXT NOT NULL)"
    )
    instance.execute("INSERT INTO authz_roles (user_id, role) VALUES (1, 'admin')")
    with instance._query_cache_lock:
        instance._query_cache.clear()
    yield instance
    instance.close()
    Database.reset()


def _role(db):
    row = db.fetch_one("SELECT role FROM authz_roles WHERE user_id = ?", (1,))
    assert row is not None
    return row["role"]


class _PgSimpleProtocolCursor:
    """Emulate pg8000 simple-protocol execution against a real SQLite cursor.

    PostgreSQL splits the string on ``;`` using its SQL lexer and runs every
    statement. ``EXPLAIN ANALYZE`` additionally *executes* its target statement
    (bare ``EXPLAIN`` only plans it). Strip the EXPLAIN modifiers so the real
    target runs against SQLite -- the demotion then genuinely commits.
    """

    def __init__(self, real):
        self._real = real

    def execute(self, sql, params=None):
        for part in sql.split(";"):
            stmt = part.strip()
            if not stmt:
                continue
            target = re.sub(
                r"^EXPLAIN\s+(?:\(\s*[^)]*\)\s+|ANALYZE\s+|VERBOSE\s+)*",
                "",
                stmt,
                flags=re.IGNORECASE,
            )
            self._real.execute(target)
        return self

    def __getattr__(self, name):
        return getattr(self._real, name)


class _PgSimpleProtocolConn:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return _PgSimpleProtocolCursor(self._real.cursor())

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_helper_is_non_vacuous():
    """EXPLAIN ANALYZE write is a write; a plain SELECT is still a read."""
    assert _query_mutates_data(f"EXPLAIN ANALYZE {DEMOTE}") is True
    assert _query_mutates_data(f"explain analyze {DEMOTE}") is True
    assert _query_mutates_data(f"EXPLAIN (ANALYZE, VERBOSE) {DEMOTE}") is True
    assert (
        _query_mutates_data("SELECT role FROM authz_roles WHERE user_id = ?") is False
    )


@pytest.mark.parametrize(
    "payload",
    [
        f"EXPLAIN ANALYZE {DEMOTE}",
        f"explain analyze {DEMOTE}",
        f"EXPLAIN (ANALYZE, VERBOSE) {DEMOTE}",
    ],
)
def test_explain_analyze_write_invalidates_cached_select(db, payload):
    """A cached 'admin' verdict must not survive an EXPLAIN ANALYZE demotion."""
    assert _role(db) == "admin"  # warm the query-result cache

    real_get_conn = db._get_conn
    db._get_conn = lambda auto_connect=True: _PgSimpleProtocolConn(
        real_get_conn(auto_connect)
    )
    try:
        cursor = db.execute(payload)
        cursor.close()
    finally:
        db._get_conn = real_get_conn

    # Prove the demotion really committed (not a no-op)...
    verify = real_get_conn().cursor()
    verify.execute("SELECT role FROM authz_roles WHERE user_id = 1")
    assert verify.fetchone()[0] == "user"
    verify.close()

    # ...and that the cached SELECT no longer serves the stale 'admin' row.
    assert _role(db) == "user"
