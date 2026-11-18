"""Regression: PostgreSQL nested block comments must not bypass the guard.

The old ``_normalize_sql`` used a non-greedy ``/\\*.*?\\*/`` substitution, so
it stopped at the first ``*/``. PostgreSQL nests block comments, which let a
payload hide ``FROM`` (or plant a fake ``WHERE``) in the still-open outer
comment while the stripped text looked like a harmless unscoped wipe to the
denylist. The guard now tracks block-comment depth with a state machine.
"""

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]


@pytest.mark.parametrize(
    "statement",
    [
        # The flagged evasion: FROM inside the outer (nested) comment.
        "DELETE /* a /* b */ FROM t */ FROM t",
        "DELETE/*a/*b*/c*/FROM/*x*/t",
        # A fake WHERE that the database treats as comment text. The old
        # non-greedy strip left it visible and wrongly accepted the wipe.
        "DELETE /* outer /* inner */ WHERE id = ? */ FROM t",
        "DELETE /* a /* WHERE id = ? */ FROM t */ FROM t",
        "DELETE FROM t /* WHERE id = ? /* nested WHERE */ */",
        # Other destructive verbs hidden the same way.
        "DROP /* a /* b */ TABLE t */ TABLE t",
        "TRUNCATE /* a /* b */ x */ t",
    ],
)
def test_nested_comments_cannot_hide_destructive_sql(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM auth_users WHERE note = 'a -- b /* c */' AND id = ?",
        "DELETE FROM auth_users WHERE note = '/* a /* b */' AND id = ?",
        "DELETE FROM auth_users WHERE id = ? -- /* nested */ trailing",
        "DELETE FROM auth_users WHERE id = ? /* WHERE id = ? */",
        "DELETE FROM auth_users WHERE id = ? /* a /* nested */ b */",
    ],
)
def test_scoped_and_comment_shaped_literals_still_accepted(statement):
    """A real WHERE clause in code is never flagged, whatever the comments."""
    from src.core.selftest.db_guard import assert_non_destructive

    assert_non_destructive(statement)


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        # Previously blocked forms are unchanged.
        ("DELETE/**/FROM/* c */auth_users -- trailing", "DELETE FROM auth_users"),
        ("DROP/*x*/TABLE/*x*/t", "DROP TABLE t"),
        # Nested comments collapse to one separator when depth returns to 0.
        ("DELETE /* a /* b */ FROM t */ FROM t", "DELETE FROM t"),
        ("DELETE /* outer /* inner */ WHERE id = ? */ FROM t", "DELETE FROM t"),
        ("DELETE /*1/*2/*3*/2*/1*/ FROM t", "DELETE FROM t"),
        # An unterminated (possibly nested) comment swallows the tail.
        ("DELETE FROM t /* a /* b */", "DELETE FROM t"),
        # A -- line comment is still stripped, including block-comment markers.
        ("DELETE FROM t -- /* fake */ WHERE id = ?", "DELETE FROM t"),
    ],
)
def test_normalize_sql_handles_nesting_and_line_comments(statement, expected):
    from src.core.selftest.db_guard import _normalize_sql

    assert _normalize_sql(statement) == expected
