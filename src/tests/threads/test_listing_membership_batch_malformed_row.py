"""Batched live-roster gate must not let a malformed row vouch for a server.

``_live_server_member_ids`` replaced a per-row ``_is_server_member`` probe
with one ``SELECT server_id FROM srv_members WHERE user_id = ?`` and a set
lookup. Its docstring promises that the allow set holds only server ids that
still have a live ``srv_members`` row, and that anything unverifiable is
absent (no proof = no grant).

Coercing each row's ``server_id`` with ``int()`` broke that promise: a
malformed row (a float, a bool, or a non-canonical string) could be coerced
into a wanted server id and admitted to the allow set even though the row is
not proof of membership in that server -- the per-row SQL probe this replaced
compared the raw value and would have denied it. That is a fail-OPEN on a
malformed row: a private thread becomes visible to a caller whose live
membership was never verified.

Only genuine integer ids from live rows may count as proof.
"""

import pytest

from src.core.threads.manager.permissions import ThreadPermissionMixin

pytestmark = pytest.mark.unit

_VIEWER_ID = 42
_SERVER_ID = 5


class _DB:
    """Minimal fetch_all stub returning a fixed set of srv_members rows."""

    def __init__(self, rows):
        self.rows = rows

    def fetch_all(self, sql, params=()):
        assert "srv_members" in sql
        assert params == (_VIEWER_ID,)
        return self.rows


def _allow_set(rows, wanted):
    mgr = ThreadPermissionMixin()
    mgr._db = _DB(rows)
    return mgr._live_server_member_ids(_VIEWER_ID, wanted)


@pytest.mark.parametrize(
    "malformed_sid, coerced",
    [
        ("05", _SERVER_ID),  # leading zero: int() -> 5, not equal as stored
        (" 5 ", _SERVER_ID),  # padded string
        (5.9, _SERVER_ID),  # float truncates to 5
        (True, 1),  # bool coerces to 1
        ("1", 1),  # numeric string
    ],
)
def test_malformed_membership_row_does_not_grant_server(malformed_sid, coerced):
    """A malformed srv_members.server_id must never enter the allow set.

    With the old ``int(row["server_id"])`` coercion each of these rows
    produced ``{coerced}``, admitting a thread for a server the row does not
    actually prove membership in.
    """
    allow = _allow_set([{"server_id": malformed_sid}], {coerced})
    assert allow == set(), (
        f"malformed row {malformed_sid!r} was coerced into a grant for {coerced}"
    )


def test_genuine_integer_membership_row_still_grants():
    """The gate still admits a real live membership (test is non-vacuous)."""
    assert _allow_set([{"server_id": _SERVER_ID}], {_SERVER_ID}) == {_SERVER_ID}


def test_missing_and_none_rows_do_not_grant():
    """A missing or NULL server_id is no proof (fail closed)."""
    assert _allow_set([{}], {_SERVER_ID}) == set()
    assert _allow_set([{"server_id": None}], {_SERVER_ID}) == set()


def test_query_error_fails_closed(monkeypatch):
    """A failed batched lookup yields no grants, not a permissive default."""
    import src.core.threads.manager.permissions as permissions

    monkeypatch.setattr(permissions.logger, "error", lambda *a, **k: None)

    class _FailingDB:
        def fetch_all(self, sql, params=()):
            raise RuntimeError("srv_members unavailable")

    mgr = ThreadPermissionMixin()
    mgr._db = _FailingDB()
    assert mgr._live_server_member_ids(_VIEWER_ID, {_SERVER_ID}) == set()
