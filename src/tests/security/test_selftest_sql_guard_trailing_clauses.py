"""Regression: trailing SQL clauses must not bypass the destructive guard.

The old unscoped-DELETE denylist was end-anchored (``DELETE FROM t$`` with an
optional trailing ``;...``). PostgreSQL/SQLite DELETE statements can carry
clauses after the table name, so a whole-table wipe such as
``DELETE FROM t RETURNING *`` or ``DELETE FROM t ORDER BY id LIMIT 10`` no
longer matched the pattern and sailed through the guard. The guard now keys on
the presence of a genuine WHERE scope restriction instead of where the
statement ends.
"""

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM auth_users RETURNING *",
        "DELETE FROM t RETURNING id",
        "DELETE FROM t USING other",
        "DELETE FROM t ORDER BY id LIMIT 10",
        "DELETE FROM t LIMIT 1",
        "DELETE FROM t OFFSET 5",
        # Comments used as separators must not hide the trailing-clause wipe.
        "DELETE/**/FROM/**/t/**/RETURNING/**/*",
        "DELETE /* c */ FROM t ORDER BY id LIMIT 10 -- where id = ?",
        # A WHERE that exists only inside a comment is not a scope restriction.
        "DELETE FROM t /* WHERE id = ? */ RETURNING *",
        "DELETE FROM t RETURNING * -- WHERE id = ?",
    ],
)
def test_whole_table_wipe_with_trailing_clause_is_rejected(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM auth_users WHERE id = ? RETURNING *",
        "DELETE FROM t USING other WHERE t.id = other.id RETURNING t.id",
        "DELETE FROM t WHERE id = ? ORDER BY id LIMIT 10",
        "DELETE FROM auth_users WHERE id = ? AND username LIKE 'selftest_%'",
        "UPDATE auth_users SET permissions = '{}' WHERE id = ?",
    ],
)
def test_genuinely_scoped_delete_with_trailing_clause_is_accepted(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    assert_non_destructive(statement)
