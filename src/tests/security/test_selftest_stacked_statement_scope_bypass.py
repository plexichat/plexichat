"""Regression: a foreign WHERE must not scope a whole-table DELETE.

The destructive guard used to look for a ``WHERE`` anywhere in the normalized
statement. That made its scope check unsound in two ways:

* a stacked, harmless second statement could lend its WHERE to an unscoped
  DELETE (``DELETE FROM t; SELECT 1 WHERE 1 = 1``);
* a WHERE nested inside a CTE / subquery / RETURNING expression is not a
  scope restriction for the DELETE at the top level.

The guard now splits normalized statements on top-level semicolons and
requires a WHERE at the DELETE's own nesting level, so neither trick can
hide a whole-table wipe. The genuinely scoped cases below prove the check
was hardened, not weakened.
"""

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]


# Each of these is a whole-table DELETE in the eyes of the database. The only
# WHERE text belongs to another statement or is nested in a subquery.
@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM auth_users; SELECT 1 WHERE 1 = 1",
        "DELETE FROM t; SELECT * FROM x WHERE id = ?",
        "DELETE FROM t RETURNING *; SELECT 1 WHERE 1 = 1",
        "DELETE FROM t; UPDATE x SET y = 1 WHERE id = ?",
        "SELECT 1 WHERE 1 = 1; DELETE FROM auth_users",
        # A WHERE inside a comment is stripped by the normalizer.
        "DELETE FROM t; SELECT 1 /* WHERE id = ? */",
        # Multiple statements: the scoped DELETE must not excuse the wipe.
        "DELETE FROM t WHERE id = ?; DELETE FROM auth_users",
        "DELETE FROM auth_users; DELETE FROM t WHERE id = ?",
        # Escape-string trick stacked with a scoped statement.
        "DELETE FROM t E'x\\' WHERE y'; SELECT 1 WHERE 1 = 1",
    ],
)
def test_stacked_where_cannot_scope_a_separate_delete(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        # The WHERE is inside the CTE, not on the DELETE.
        "WITH x AS (SELECT 1 WHERE 1 = 1) DELETE FROM t",
        # The WHERE is inside a RETURNING subquery.
        "DELETE FROM t RETURNING (SELECT 1 WHERE 1 = 1)",
        "DELETE FROM t USING (SELECT 1 WHERE 1 = 1) s",
        # A nested WHERE plus a comment-shaped fake at top level.
        "WITH x AS (SELECT 1 WHERE 1) DELETE FROM t /* WHERE id = ? */",
    ],
)
def test_nested_where_cannot_scope_the_delete(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        # A top-level WHERE still scopes a single DELETE.
        "DELETE FROM auth_users WHERE id = ? AND username LIKE 'selftest_%'",
        "DELETE FROM t USING other WHERE t.id = other.id RETURNING t.id",
        "DELETE FROM t WHERE id = ? ORDER BY id LIMIT 10",
        # ScopedWHERE in a subquery is fine as long as the DELETE has its own.
        "DELETE FROM t WHERE id IN (SELECT id FROM x WHERE flag = 1)",
        "WITH x AS (SELECT 1 WHERE 1) DELETE FROM t WHERE id = ?",
        # A stacked harmless statement with its own WHERE does not affect a
        # properly scoped DELETE.
        "DELETE FROM t WHERE id = ?; SELECT 1 WHERE 1 = 1",
        # Literals/comments that merely look like WHERE text are inert.
        "DELETE FROM auth_users WHERE note = 'WHERE' AND id = ?",
        "DELETE FROM auth_users WHERE note = $$/* WHERE */$$ AND id = ?",
        "UPDATE auth_users SET permissions = '{}' WHERE id = ?",
    ],
)
def test_genuinely_scoped_statements_are_still_accepted(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    assert_non_destructive(statement)


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        (
            "DELETE FROM t; SELECT 1 WHERE 1 = 1",
            ["DELETE FROM t", "SELECT 1 WHERE 1 = 1"],
        ),
        ("DELETE FROM t WHERE id = ?", ["DELETE FROM t WHERE id = ?"]),
        ("DELETE FROM t 'a;b'", ["DELETE FROM t"]),
    ],
)
def test_split_statements_only_splits_on_top_level_semicolons(statement, expected):
    from src.core.selftest.db_guard import _normalize_sql, _split_statements

    assert _split_statements(_normalize_sql(statement)) == expected
