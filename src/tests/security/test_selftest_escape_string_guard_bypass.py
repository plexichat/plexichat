"""Regression: escape-string literals must not bypass the destructive guard.

``_normalize_sql`` blanked ordinary single-quoted literals but assumed a
backslash was plain data. PostgreSQL escape strings (``E'...'`` / ``e'...'``)
treat ``\\'`` as an escaped quote, so a whole-table wipe could hide its only
``WHERE`` *inside* one literal::

    DELETE FROM auth_users E'x\\' WHERE y'

The database sees a single string literal (``x' WHERE y``) and no WHERE
clause -- an unscoped wipe -- while the old normalizer closed the literal at
the backslash-escaped quote and exposed a fake ``WHERE`` that disabled the
denylist. Ordinary strings behave the same way when
``standard_conforming_strings = off``.

The guard now screens both plausible literal grammars (backslash as data and
backslash as escape) and refuses a destructive reading under either. These
tests lock that invariant, pin the normalizer output for each mode, and prove
legitimately scoped statements (including escaped-quote/backslash literals)
are still accepted -- the checks are hardened, not weakened.
"""

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]


# The database reads each of these as an unscoped whole-table DELETE: the
# only WHERE-looking text is inside an E'' literal whose quote is escaped by
# a backslash. The old single-grammar normalizer accepted every one of them.
_ESCAPE_STRING_WIPES = [
    "DELETE FROM t E'x\\' WHERE y'",
    "DELETE FROM auth_users E'x\\' WHERE y'",
    "DELETE FROM t e'it\\'s WHERE id=?'",
    "DELETE FROM t E'x\\' WHERE 1=1' RETURNING *",
    "DELETE FROM t E'x\\' WHERE id=?' ORDER BY id LIMIT 10",
    "DELETE /* c */ FROM t E'x\\' WHERE y'",
    "DELETE/**/FROM/**/t/**/E'x\\' WHERE y'",
    "WITH x AS (SELECT 1) DELETE FROM t E'x\\' WHERE y'",
]


@pytest.mark.parametrize("statement", _ESCAPE_STRING_WIPES)
def test_escape_string_smuggled_where_is_rejected(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        # An escaped-quote literal must not open a comment that hides a wipe.
        "SELECT E'x\\' /*'; DELETE FROM auth_users",
        # A stacked wipe after an ordinary trailing-backslash literal must
        # still be caught by the plain (backslash-as-data) scan -- the new
        # escape-aware variant must not mask it.
        "SELECT 'a\\'; DELETE FROM auth_users",
        "SELECT 'a\\'; DELETE FROM auth_users WHERE id = 1; TRUNCATE t",
    ],
)
def test_stacked_wipes_are_still_detected(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        # A genuine WHERE outside the literal always scopes the DELETE.
        "DELETE FROM auth_users WHERE id = ? AND username LIKE 'selftest_%'",
        "DELETE FROM auth_users WHERE note = E'it\\'s fine' AND id = ?",
        "DELETE FROM auth_users WHERE note = e'C:\\\\' AND id = ?",
        "DELETE FROM auth_users WHERE note = 'C:\\' AND id = ?",
        "DELETE FROM auth_users WHERE note = E'x\\' WHERE y' AND id = ?",
        "UPDATE auth_users SET permissions = '{}' WHERE id = ?",
        "DELETE FROM auth_users WHERE note = $$/* WHERE */$$ AND id = ?",
    ],
)
def test_genuinely_scoped_escape_literals_still_accepted(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    assert_non_destructive(statement)


@pytest.mark.parametrize(
    ("statement", "plain", "escaped"),
    [
        (
            "DELETE FROM t E'x\\' WHERE y'",
            "DELETE FROM t E WHERE y",
            "DELETE FROM t E",
        ),
        (
            "DELETE FROM t e'it\\'s WHERE id=?'",
            "DELETE FROM t e s WHERE id=?",
            "DELETE FROM t e",
        ),
        # An escaped backslash is data in both modes; the following quote
        # still closes the literal, so both agree here.
        (
            "DELETE FROM t E'C:\\\\' WHERE id = ?",
            "DELETE FROM t E WHERE id = ?",
            "DELETE FROM t E WHERE id = ?",
        ),
        # Doubled quotes stay inside the literal in both modes.
        (
            "DELETE FROM t E'it''s WHERE y'",
            "DELETE FROM t E",
            "DELETE FROM t E",
        ),
    ],
)
def test_normalize_sql_literal_grammars(statement, plain, escaped):
    from src.core.selftest.db_guard import _normalize_sql

    assert _normalize_sql(statement) == plain
    assert _normalize_sql(statement, escape_backslashes=True) == escaped
