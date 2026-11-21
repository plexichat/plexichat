"""Regression: a stacked write behind a PostgreSQL ``E'...'`` escape string
must still invalidate the database query-result cache.

``_SQL_NON_CODE_RE`` used to mask single-quoted literals with the plain pattern
``'(?:[^']|'')*'``. In an E-string a backslash escapes the next character, so
``E'\\''`` is ONE literal holding a single quote. The plain pattern instead read
that ``\\'`` as a doubled quote and kept scanning past the literal's real end,
pairing its closing quote with the quote before a following DML literal::

    SELECT E'\\''; UPDATE authz_roles SET role='user'

pg8000 sends parameterless SQL through PostgreSQL's simple-query protocol,
which executes every ``;``-separated statement. PG runs the SELECT and then the
UPDATE, but the over-matching literal pattern swallowed the ``;`` so
``_has_multiple_statements()`` saw a single read. The query cache kept serving
a stale authorization verdict (the pre-demotion ``admin`` role) even though the
demotion had committed.

The fix adds a backslash-aware ``[Ee]'(?:[^'\\\\]|\\\\.|'')*'`` alternative ahead
of the plain single-quote pattern, so E-strings end where PG says they do and a
stacked ``;`` stays visible.
"""

import pytest
import utils.config as config
from src.core.database.core import Database
from src.core.database.core.execution import (
    _has_multiple_statements,
    _query_mutates_data,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

# The exact shape from the audit: an E-string whose backslash-escaped quote used
# to desynchronise the literal matcher and hide the stacked UPDATE's separator.
PAYLOAD = "SELECT E'\\''; UPDATE authz_roles SET role='user'"

# SQLite has no E'' syntax, so the faithful PG simple-protocol emulation below
# rewrites this one opaque E-literal to the equivalent SQLite string (a single
# quote) before splitting on ``;`` and running each part, exactly as pg8000's
# execute_simple lets the server execute every separated statement.
_E_LITERAL_AS_SQLITE = "''''"


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


def test_escaped_e_string_stacked_write_is_classified_as_write():
    assert _query_mutates_data(PAYLOAD) is True
    assert _has_multiple_statements(PAYLOAD) is True


def test_non_vacuous_escaped_e_string_reads_stay_reads():
    # A lone E-string is a genuine read ...
    assert _query_mutates_data("SELECT E'\\''") is False
    # ... and a ';' inside an E-string body is literal data, not a separator.
    assert _query_mutates_data("SELECT E'a;b'") is False
    # The plain single-quote path must be unaffected by the new alternative.
    assert _query_mutates_data("SELECT 'a;b'") is False
    # Sanity: a plain stacked write is still detected (no regression).
    assert (
        _query_mutates_data("SELECT 1; UPDATE authz_roles SET role='user'") is True
    )


def test_escaped_e_string_stacked_write_invalidates_cached_select(db):
    """A cached 'admin' verdict must not survive a demotion hidden this way."""
    assert _role(db) == "admin"  # warms the query-result cache

    class _StackingCursor:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=None):
            # Model PG's simple query protocol: the E-literal is one opaque
            # string, so translate it, then execute every ';'-separated part.
            translated = sql.replace("E'\\''", _E_LITERAL_AS_SQLITE)
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
        cursor = db.execute(PAYLOAD)
        cursor.close()
    finally:
        db._get_conn = real_get_conn

    # Prove the stacked UPDATE really committed (not a no-op) ...
    verify = real_get_conn().cursor()
    verify.execute("SELECT role FROM authz_roles WHERE user_id = 1")
    assert verify.fetchone()[0] == "user"
    verify.close()

    # ... and that the cached SELECT no longer serves the stale 'admin' row.
    assert _role(db) == "user"


def test_cr_terminated_line_comment_does_not_hide_a_write():
    """PG's scanner ends ``--`` comments at ``\\r`` as well as ``\\n``.

    ``_strip_leading_sql_comments`` used to look only for ``\\n``, so a write
    following ``-- c\\r`` was dropped with the comment and classified as a read.
    ``_SQL_NON_CODE_RE`` likewise let a ``;`` after ``\\r`` look commented out.
    Both now treat ``\\r`` as a line terminator (fail closed).
    """
    demote = "UPDATE authz_roles SET role='user'"

    # A direct write after a CR-terminated comment executes on PG.
    assert _query_mutates_data(f"-- c\r{demote}") is True
    # A stacked write after a CR-terminated line comment executes on PG too.
    assert _query_mutates_data(f"SELECT 1 -- c\r; {demote}") is True
    assert _has_multiple_statements(f"SELECT 1 -- c\r; {demote}") is True

    # Non-vacuous: LF (and CRLF) comments still terminate correctly, and a
    # genuine read behind a CR-terminated comment stays a read.
    assert _query_mutates_data(f"-- c\n{demote}") is True
    assert _query_mutates_data(f"-- c\r\n{demote}") is True
    assert _query_mutates_data("-- c\rSELECT 1") is False
