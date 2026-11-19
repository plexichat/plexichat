"""Regression: quoted literals must not bypass the non-destructive SQL guard.

``_normalize_sql`` used to strip comments but treat string literals as ordinary
text. That let a whole-table wipe carry its only ``WHERE`` inside a literal
(``DELETE FROM t RETURNING 'WHERE'``, ``DELETE FROM t USING (SELECT 'WHERE') x``)
and pass the scope check, or hide a destructive statement behind a ``/*`` that
was really inside a string (``SELECT '/*'; DELETE FROM t``). A quoted identifier
named ``"WHERE"`` faked the same scope restriction.

The normalizer now blanks quoted literal/identifier contents so only real SQL
keywords can satisfy the denylist. These tests lock that invariant and confirm
genuinely scoped cleanup statements still pass (the guard is not weakened).
"""

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]


@pytest.mark.parametrize(
    "statement",
    [
        # A RETURNING literal is not a WHERE scope restriction.
        "DELETE FROM t RETURNING 'WHERE'",
        "DELETE FROM auth_users RETURNING 'WHERE'",
        # USING / CTE subqueries with a literal marker still wipe the table.
        "DELETE FROM t USING (SELECT 'WHERE' AS marker) m",
        "WITH x AS (SELECT 'WHERE' AS w) DELETE FROM t USING x",
        # A bare literal after the table name is not a scope either.
        "DELETE FROM t 'WHERE'",
        # A quoted identifier cannot manufacture a WHERE keyword.
        'DELETE FROM "WHERE"',
        # PostgreSQL dollar-quoted (and tagged dollar-quoted) literals.
        "DELETE FROM t RETURNING $$WHERE$$",
        "DELETE FROM t RETURNING $tag$WHERE$tag$",
        # Escaped doubled quotes inside a literal must not split it open.
        "DELETE FROM t RETURNING 'it''s WHERE'",
        # A comment opener inside a literal must not swallow a stacked wipe.
        "SELECT '/*'; DELETE FROM auth_users",
        "SELECT '/*'; DROP TABLE auth_users",
        # The literal must not hide a trailing-clause wipe from the guard.
        "DELETE FROM t /* c */ RETURNING 'WHERE'",
    ],
)
def test_literal_keywords_cannot_fake_scope_or_hide_sql(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        # A real WHERE outside the literal still scopes the delete.
        "DELETE FROM auth_users WHERE note = 'WHERE' AND id = ?",
        "DELETE FROM auth_users WHERE note = 'a -- b /* c */' AND id = ?",
        "DELETE FROM auth_users WHERE note = '/* a /* b */' AND id = ?",
        "DELETE FROM auth_users WHERE id = ? AND username LIKE 'selftest_%'",
        # Quoted identifiers that are not keywords are unaffected.
        'DELETE FROM "auth_users" WHERE id = ?',
        # Comment-shaped text in a dollar-quoted literal is inert.
        "DELETE FROM auth_users WHERE note = $$/* WHERE */$$ AND id = ?",
        # A real WHERE with a trailing RETURNING clause is still scoped.
        "DELETE FROM auth_users WHERE note = 'x' RETURNING id",
    ],
)
def test_scoped_statements_with_literals_are_still_accepted(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    assert_non_destructive(statement)


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("DELETE FROM t RETURNING 'WHERE'", "DELETE FROM t RETURNING"),
        ("DELETE FROM t 'WHERE'", "DELETE FROM t"),
        ("SELECT '/*'; DELETE FROM t", "SELECT ; DELETE FROM t"),
        ('DELETE FROM "WHERE"', 'DELETE FROM "x"'),
        ("DELETE FROM t RETURNING $$WHERE$$", "DELETE FROM t RETURNING"),
        ("DELETE FROM t RETURNING $tag$WHERE$tag$", "DELETE FROM t RETURNING"),
        # Doubled quotes stay inside the literal until its real close.
        ("DELETE FROM t RETURNING 'it''s WHERE'", "DELETE FROM t RETURNING"),
        # An unterminated literal swallows the tail like the parser does.
        ("DELETE FROM t 'WHERE", "DELETE FROM t"),
    ],
)
def test_normalize_sql_blanks_quoted_regions(statement, expected):
    from src.core.selftest.db_guard import _normalize_sql

    assert _normalize_sql(statement) == expected
