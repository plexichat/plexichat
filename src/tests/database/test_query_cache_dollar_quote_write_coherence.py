"""Regression: a stacked write hidden behind PostgreSQL dollar-quoted bodies
must still invalidate the database query-result cache.

``_query_mutates_data()`` blanks quoted literals, quoted identifiers and
comments before counting ``;`` separators so a semicolon inside ``'a; b'`` does
not look like a second statement. Dollar-quoted bodies (``$$...$$`` /
``$tag$...$tag$``) were NOT blanked. A lone quote inside one then paired with a
quote outside it, swallowing the genuine separators in e.g.::

    SELECT $$'$$; UPDATE authz_roles SET role = 'user' WHERE user_id = 1; SELECT $$'$$

pg8000 sends parameterless statements through PostgreSQL's simple-query
protocol, which executes every ``;``-separated part. The UPDATE committed but
the string was classified as a single read, so the query cache kept serving a
stale authorization verdict (the pre-demotion ``admin`` role). Dollar-quoted
bodies are now blanked as opaque literals before the separator count.
"""

import pytest
import utils.config as config
from src.core.database.core import Database
from src.core.database.core.execution import _query_mutates_data

pytestmark = [pytest.mark.unit, pytest.mark.security]

# A single read followed by a real write, bracketed by dollar-quoted bodies
# that each hold one lone quote. That quote desynchronised the old ``'...'``
# matcher and hid both ';' separators from the stacked-statement check.
STACKED_WRITE = (
    "SELECT $$'$$; UPDATE authz_roles SET role = 'user' WHERE user_id = 1; SELECT $$'$$"
)


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


def test_helper_non_vacuous_dollar_quote_classification():
    """The payload is a write; a lone dollar-quoted SELECT is still a read."""
    assert _query_mutates_data(STACKED_WRITE) is True
    assert _query_mutates_data("SELECT $$'$$") is False
    assert _query_mutates_data("SELECT $tag$a; b$tag$") is False


def test_stacked_write_behind_dollar_quotes_invalidates_cached_select(db):
    """A cached 'admin' verdict must not survive a demotion hidden this way."""
    assert _role(db) == "admin"  # warms the query-result cache

    # SQLite has no dollar-quoting and rejects multiple statements per cursor,
    # so emulate pg8000's execute_simple: translate the opaque dollar-quoted
    # bodies to literal '' and run each ';'-separated part in turn. The middle
    # statement is a real UPDATE that commits against SQLite.
    class _StackingCursor:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=None):
            translated = sql.replace("$$'$$", "''")
            for stmt in (part.strip() for part in translated.split(";")):
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
        cursor = db.execute(STACKED_WRITE)
        cursor.close()
    finally:
        db._get_conn = real_get_conn

    # Prove the stacked UPDATE really committed (not a no-op)...
    verify = real_get_conn().cursor()
    verify.execute("SELECT role FROM authz_roles WHERE user_id = 1")
    assert verify.fetchone()[0] == "user"
    verify.close()

    # ...and that the cached SELECT no longer serves the stale 'admin' row.
    assert _role(db) == "user"
