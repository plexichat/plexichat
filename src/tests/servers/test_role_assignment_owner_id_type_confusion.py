"""Regression: owner hierarchy gate must not be bypassed by a mismatched id type.

``_guard_member_target`` compares ``server.owner_id`` (projected from the
``srv_servers`` DB row) to ``member_user_id`` (resolved from the ``srv_members``
DB row) with loose ``==``. If the target id arrives as a text/float encoded
value (``"5"``, ``5.9``) while the owner id is a genuine ``int`` ``5``, the
comparison is False, ``target_is_owner`` is False, and a moderator holding
``members.manage_roles`` can edit the server owner's roles -- a hierarchy
bypass. The "owner is untouchable" rule must hold regardless of how an id is
encoded in the row.
"""

from types import SimpleNamespace

import pytest

from src.core.servers.exceptions import RoleHierarchyError
from src.core.servers.handlers.roles.assignments import RoleAssignmentMixin


def _handler(owner_id, actor_roles=None, target_roles=None):
    """Build a minimal handler whose manager returns a fixed server + roles."""
    if actor_roles is None:
        actor_roles = [{"position": 5}]
    if target_roles is None:
        target_roles = [{"position": 1}]

    server = SimpleNamespace(owner_id=owner_id)

    def get_server(server_id, user_id):
        return server

    def get_member_role_rows(server_id, user_id):
        # The actor outranks the target, so without the owner guard
        # can_manage_member() returns True and the edit is allowed.
        return actor_roles if user_id == 2 else target_roles

    manager = SimpleNamespace(
        get_server=get_server, _get_member_role_rows=get_member_role_rows
    )
    return SimpleNamespace(manager=manager)


@pytest.mark.parametrize(
    "owner_id,target_id",
    [
        (5, "5"),  # TEXT-affinity column returns the owner id as a string
        (5, 5.9),  # REAL-affinity column returns a float-coded owner id
    ],
)
def test_owner_target_with_mismatched_id_type_is_rejected(owner_id, target_id):
    """A non-owner must not edit the owner's roles via an encoded id."""
    handler = _handler(owner_id)
    # Pre-fix: `5 == "5"` / `5 == 5.9` is False -> target_is_owner False ->
    # can_manage_member() allows the edit -> no exception (fail open).
    with pytest.raises(RoleHierarchyError):
        RoleAssignmentMixin._guard_member_target(handler, 2, 1, target_id)


def test_non_owner_target_with_int_id_is_still_allowed():
    """Control: the gate must not blanket-deny legitimate role edits."""
    handler = _handler(5)
    # target 3 is a genuine non-owner int id; the actor (position 5) outranks
    # them (position 1), so the edit is allowed both before and after the fix.
    RoleAssignmentMixin._guard_member_target(handler, 2, 1, 3)
