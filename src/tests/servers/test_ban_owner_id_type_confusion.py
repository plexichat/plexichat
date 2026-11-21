"""Regression: ban/unban owner hierarchy gate must not be bypassed by id type confusion.

``ban_member`` guards the "owner is untouchable" rule with a loose
``server.owner_id == member_user_id`` comparison. Both ids are authoritative DB
values: ``owner_id`` is projected from the ``srv_servers`` row, while
``member_user_id`` is the target resolved by the API route. If either is stored
as a text/float encoded value (``"5"``, ``5.9``) the loose ``==`` evaluates
False, ``CannotModifyOwnerError`` is skipped, and a moderator holding
``members.ban`` can ban/kick the server owner -- a hierarchy bypass. ``unban_member``
has the same loose ``target_is_owner`` check. The protection must hold regardless
of how an id is encoded in the row.
"""

from types import SimpleNamespace

import pytest

from src.core.servers.exceptions import CannotModifyOwnerError
from src.core.servers.handlers.members.bans import MemberBanMixin


def _handler(owner_id, banned_row=None):
    """Build a minimal handler whose manager returns a fixed server + roles."""
    server = SimpleNamespace(owner_id=owner_id)
    executed = []

    def get_server(server_id, user_id):
        return server

    def require_permission(*args, **kwargs):
        return None

    def _get_member_role_rows(server_id, user_id):
        # The actor (2) outranks the target (5/3), so without the owner guard
        # can_manage_member() is True and the sanction proceeds.
        return [{"position": 5}] if user_id == 2 else [{"position": 1}]

    def get_member(server_id, user_id):
        return SimpleNamespace(id=user_id)

    def fetch_one(sql, params):
        return banned_row

    def execute(sql, params):
        executed.append((sql, params))

    db = SimpleNamespace(fetch_one=fetch_one, execute=execute)
    manager = SimpleNamespace(
        get_server=get_server,
        require_permission=require_permission,
        _get_member_role_rows=_get_member_role_rows,
        _messaging=None,
        _invalidate_member_caches=lambda *a, **k: None,
        _get_timestamp=lambda: 1000,
        _generate_id=lambda: 999,
        _log_audit=lambda *a, **k: None,
    )
    return SimpleNamespace(
        manager=manager,
        db=db,
        get_member=get_member,
        executed=executed,
    )


@pytest.mark.parametrize("owner_id", ["5", 5.9])
def test_encoded_owner_id_cannot_be_banned(owner_id):
    """A non-owner must not ban the owner via an encoded DB id."""
    handler = _handler(owner_id)
    # Pre-fix: `"5" == 5` / `5.9 == 5` is False -> owner guard skipped ->
    # can_manage_member() allows the ban -> no exception (fail open).
    with pytest.raises(CannotModifyOwnerError):
        MemberBanMixin.ban_member(handler, 2, 1, 5)
    assert not handler.executed, "a rejected ban must not write anything"


@pytest.mark.parametrize("owner_id", ["5", 5.9])
def test_encoded_owner_id_cannot_be_unbanned(owner_id):
    """A non-owner must not unban a (protected) owner via an encoded id."""
    handler = _handler(owner_id, banned_row={"user_id": 5})
    with pytest.raises(CannotModifyOwnerError):
        MemberBanMixin.unban_member(handler, 2, 1, 5)
    assert not handler.executed, "a rejected unban must not write anything"


def test_non_owner_target_with_int_id_can_still_be_banned():
    """Control: the gate must not blanket-deny legitimate bans."""
    handler = _handler(5)
    # target 3 is a genuine non-owner int id; the actor (position 5) outranks
    # them (position 1), so the ban proceeds both before and after the fix.
    MemberBanMixin.ban_member(handler, 2, 1, 3)
    assert handler.executed, "a legitimate ban must still write the ban row"


def test_non_owner_target_with_int_id_can_still_be_unbanned():
    """Control: legitimate unbans are preserved."""
    handler = _handler(5, banned_row={"user_id": 3})
    assert MemberBanMixin.unban_member(handler, 2, 1, 3) is True
    assert handler.executed, "a legitimate unban must still write the delete"
