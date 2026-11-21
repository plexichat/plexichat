"""Regression: a write stacked behind a read in one SQL string must still
invalidate the query-result cache.

pg8000 executes parameterless statements through PostgreSQL's ``execute_simple``
(plain/simple-query protocol), which runs every ``;``-separated statement in the
string. The query-cache invalidation check looked only at the leading prefix,
so ``SELECT 1; UPDATE authz_roles SET role = 'user' ...`` was classified as a
read and left cached authorization verdicts serving the pre-write role even
though the UPDATE had committed. The check now fails closed: a string carrying
more than one statement is treated as a write and clears the cache.
"""

import pytest
import utils.config as config
from src.core.database.core import Database
from src.core.database.core.execution import _query_mutates_data

pytestmark = [pytest.mark.unit, pytest.mark.security]


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


def test_stacked_write_invalidates_cached_select(db):
    """A cached 'admin' verdict must not survive a demotion stacked after a read."""
    assert _role(db) == "admin"  # warms the query-result cache

    # Emulate pg8000's execute_simple: a parameterless statement is sent as a
    # plain query string and every ';'-separated part is executed. SQLite's own
    # cursor rejects multiple statements, so wrap it for this one call.
    class _StackingCursor:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=None):
            for stmt in (part.strip() for part in sql.split(";")):
                if stmt:
                    self._real.execute(stmt)
            return self

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _StackingConn:
        def __init__(self, real):
            self._real = real

        def cursor(self):
            return _StackingCursor(self._real.cursor())

        def __getattr__(self, name):
            return getattr(self._real, name)

    real_get_conn = db._get_conn
    db._get_conn = lambda auto_connect=True: _StackingConn(real_get_conn(auto_connect))
    try:
        cursor = db.execute(
            "SELECT 1; UPDATE authz_roles SET role = 'user' WHERE user_id = 1"
        )
        cursor.close()
    finally:
        db._get_conn = real_get_conn

    # Prove the stacked UPDATE really committed (the statement is not a no-op)...
    verify = real_get_conn().cursor()
    verify.execute("SELECT role FROM authz_roles WHERE user_id = 1")
    assert verify.fetchone()[0] == "user"
    verify.close()

    # ...and that the cached SELECT no longer serves the stale 'admin' row.
    assert _role(db) == "user"


def test_helper_is_non_vacuous(db):
    """A single SELECT is a read; a stacked SELECT/UPDATE is a write."""
    assert (
        _query_mutates_data("SELECT role FROM authz_roles WHERE user_id = ?") is False
    )
    assert (
        _query_mutates_data(
            "SELECT 1; UPDATE authz_roles SET role = 'user' WHERE user_id = 1"
        )
        is True
    )
