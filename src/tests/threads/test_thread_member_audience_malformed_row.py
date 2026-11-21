"""Private-thread gateway audience must not admit malformed member rows.

``get_all_member_user_ids`` is the authoritative audience for private-thread
gateway events: emitters scope dispatch to exactly the ids it returns instead
of falling back to "all members of the server". It builds that audience from a
broad ``thread_members``/``srv_members`` join projection and originally coerced
every row's ``user_id`` with ``int()``.

A malformed-but-coercible value (``"05"``, a padded string, a float ``5.9``,
``True``) silently became a real user id and entered the audience even though
the row does not prove that user's live membership. That leaked private thread
name/owner/existence and message/typing activity to a user who was not really
a member -- a fail-OPEN on a malformed row. Only genuine integer ids from the
live roster may enter the audience.
"""

import pytest

from src.core.threads.manager.membership import ThreadMembershipMixin

pytestmark = pytest.mark.unit

_THREAD_ID = 42
_SERVER_ID = 5


class _DB:
    """Minimal DB stub: a backing server plus a fixed set of joined rows."""

    def __init__(self, rows, server_id=_SERVER_ID):
        self.rows = rows
        self.server_id = server_id

    def fetch_one(self, sql, params=()):
        assert "thread_threads" in sql
        assert params == (_THREAD_ID,)
        return {"server_id": self.server_id}

    def fetch_all(self, sql, params=()):
        assert "thread_members" in sql
        assert "srv_members" in sql
        assert params == (self.server_id, _THREAD_ID)
        return self.rows


def _audience(rows):
    mgr = ThreadMembershipMixin()
    mgr._db = _DB(rows)
    return mgr.get_all_member_user_ids(_THREAD_ID)


@pytest.mark.parametrize(
    "malformed_uid, coerced",
    [
        ("05", 5),  # leading zero: int() -> 5
        (" 5 ", 5),  # padded string
        (5.9, 5),  # float truncates to 5
        (True, 1),  # bool coerces to 1
        ("1", 1),  # numeric string
    ],
)
def test_malformed_member_row_does_not_enter_private_thread_audience(
    malformed_uid, coerced
):
    """A malformed user_id must never be coerced into a grant."""
    audience = _audience([{"user_id": malformed_uid}])
    assert coerced not in audience, (
        f"malformed row {malformed_uid!r} was coerced into audience member {coerced}"
    )
    assert audience == []


def test_genuine_integer_member_row_still_grants():
    """The audience gate still admits a real member (test is non-vacuous)."""
    assert _audience([{"user_id": 7}]) == [7]


def test_mixed_rows_keep_only_genuine_integers():
    """A genuine int beside malformed rows must survive without the coerced ids."""
    audience = _audience(
        [
            {"user_id": 7},
            {"user_id": "07"},
            {"user_id": True},
            {"user_id": 9.9},
            {"user_id": 11},
        ]
    )
    assert audience == [7, 11]


def test_missing_server_fails_closed():
    """If the backing server cannot be resolved, no audience is returned."""
    mgr = ThreadMembershipMixin()
    mgr._db = _DB([{"user_id": 7}], server_id=None)
    assert mgr.get_all_member_user_ids(_THREAD_ID) == []
