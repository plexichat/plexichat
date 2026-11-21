"""Regression: kick/timeout owner gates must not be bypassed by id type confusion.

``kick_member`` and ``update_member`` both guard the "owner is untouchable" rule
with a loose ``server.owner_id == member_user_id`` comparison. Both ids are
authoritative DB values (owner from ``srv_servers``, target resolved by the API
route). A text/float encoded owner id (``"5"``, ``5.9``) compares unequal to the
genuine int target, skipping the guard and letting a moderator kick/time out the
server owner.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.servers.exceptions import CannotModifyOwnerError
from src.core.servers.handlers.members.crud import MemberCrudMixin


def _handler(owner_id):
    server = SimpleNamespace(owner_id=owner_id)
    executed = []

    def get_member(server_id, user_id):
        return SimpleNamespace(
            id=user_id,
            nickname=None,
            muted=False,
            deafened=False,
            timeout_until=None,
            timeout_reason=None,
        )

    def _get_member_role_rows(server_id, user_id):
        # Actor (2) outranks target, so without the owner guard the hierarchy
        # check passes and the action proceeds.
        return [{"position": 5}] if user_id == 2 else [{"position": 1}]

    manager = SimpleNamespace(
        get_server=lambda server_id, user_id: server,
        require_permission=lambda *a, **k: None,
        _get_member_role_rows=_get_member_role_rows,
        _messaging=None,
        _invalidate_member_caches=lambda *a, **k: None,
        _invalidate_roster_caches=lambda *a, **k: None,
        _invalidate_permission_caches=lambda *a, **k: None,
        _get_timestamp=lambda: 1000,
        _log_audit=lambda *a, **k: None,
    )
    db = SimpleNamespace(execute=lambda sql, params: executed.append((sql, params)))
    return SimpleNamespace(
        manager=manager, db=db, get_member=get_member, executed=executed
    )


@pytest.mark.parametrize("owner_id", ["5", 5.9])
def test_encoded_owner_id_cannot_be_kicked(owner_id):
    handler = _handler(owner_id)
    # Pre-fix: `"5" == 5` / `5.9 == 5` is False -> owner guard skipped -> kick
    # proceeds -> no exception (fail open).
    with pytest.raises(CannotModifyOwnerError):
        MemberCrudMixin.kick_member(handler, 2, 1, 5)
    assert not handler.executed, "a rejected kick must not write anything"


@pytest.mark.parametrize("owner_id", ["5", 5.9])
def test_encoded_owner_id_cannot_be_timed_out(owner_id):
    handler = _handler(owner_id)
    with patch("src.core.servers.handlers.members.crud.invalidate_pattern"):
        with pytest.raises(CannotModifyOwnerError):
            MemberCrudMixin.update_member(handler, 2, 1, 5, timeout_until=999)
    assert not handler.executed, "a rejected timeout must not write anything"


def test_non_owner_target_can_still_be_kicked():
    handler = _handler(5)
    assert MemberCrudMixin.kick_member(handler, 2, 1, 3) is True
    assert handler.executed, "a legitimate kick must still write"


def test_non_owner_target_can_still_be_timed_out():
    handler = _handler(5)
    with patch("src.core.servers.handlers.members.crud.invalidate_pattern"):
        MemberCrudMixin.update_member(handler, 2, 1, 3, timeout_until=999)
    assert handler.executed, "a legitimate timeout must still write"
