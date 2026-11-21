"""Regression: role-override owner gate must not be bypassed by id type confusion.

``_guard_override_target`` protects the server owner from channel overrides with
a loose ``server.owner_id == target_id`` comparison (owner from ``srv_servers``,
target resolved by the API route). A text/float encoded owner id compares unequal
to the genuine int target, skipping the guard and letting a ``channels.manage``
holder deny the owner's access.
"""

from types import SimpleNamespace

import pytest

from src.core.servers.exceptions import RoleHierarchyError
from src.core.servers.handlers.roles.overrides import RoleOverrideMixin


def _handler(owner_id):
    server = SimpleNamespace(owner_id=owner_id)

    def _get_member_role_rows(server_id, user_id):
        return [{"position": 5}] if user_id == 2 else [{"position": 1}]

    manager = SimpleNamespace(
        get_server=lambda server_id, user_id: server,
        get_member=lambda server_id, user_id: SimpleNamespace(id=user_id),
        _get_member_role_rows=_get_member_role_rows,
    )
    return SimpleNamespace(manager=manager, get_permissions=lambda uid, sid: {})


@pytest.mark.parametrize("owner_id", ["5", 5.9])
def test_encoded_owner_id_cannot_be_overridden(owner_id):
    handler = _handler(owner_id)
    # Pre-fix: `"5" == 5` / `5.9 == 5` is False -> owner guard skipped ->
    # can_manage_member() passes -> no exception (fail open).
    with pytest.raises(RoleHierarchyError):
        RoleOverrideMixin._guard_override_target(handler, 2, 1, "member", 5)


def test_non_owner_target_can_still_be_overridden():
    handler = _handler(5)
    RoleOverrideMixin._guard_override_target(handler, 2, 1, "member", 3)
