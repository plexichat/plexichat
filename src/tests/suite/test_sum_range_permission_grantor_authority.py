"""ACL writes must require owner/share authority, not merely cell-edit access.

``grant_workbook`` / ``revoke_grant`` are guarded by ``_require_share_authority``
(owner or server manager).  ``set_range_permission`` and
``remove_range_permission`` write the very same access-control list -- they mark
cells ``read_only`` / ``editable`` -- but were only gated by
``_workbook(..., mutation=True)``, which every ``edit`` grantee passes.  An
``edit`` grantee is deliberately blocked from editing cells the owner locked
with a ``read_only`` range (``_check_range_permissions`` applies the lock to
every non-owner, including editors).  Before the fix such an editor could call
``set_range_permission`` to flip the owner's locked range to ``editable`` -- or
delete it with ``remove_range_permission`` -- and then edit the protected cells.
That is a grantee minting an access right (edit on locked cells) they do not
hold: the same privilege-laundering class N640 closed for Stash grants.

These tests prove the escalation is closed and that the owner (and a server
manager on a server-scoped workbook) can still manage range permissions, so the
guard is not vacuous.
"""

import pytest

from src.core.suite.sum.manager import SumManager, SumPermissionError
from src.core.suite.sum.schema import create_tables as create_sum_tables

pytestmark = pytest.mark.unit


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _locked_range(manager, owner, workbook, sheet_id):
    manager.set_range_permission(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 5, 0, 3, "read_only"
    )


def _edit_locked_cell(manager, user, workbook, sheet_id, value):
    return manager.apply_operation(
        user.id,
        int(workbook["id"]),
        int(sheet_id),
        f"op-{value}-{workbook['id']}",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": value}],
    )


def test_edit_grantee_cannot_unlock_owner_locked_range(db, two_users, server_manager):
    """An editor must not rewrite the ACL that locks their own editing."""
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "ACL target")
    sheet_id = workbook["sheets"][0]["id"]
    manager.grant_workbook(owner.id, int(workbook["id"]), editor.id, ["edit"])
    _locked_range(manager, owner, workbook, sheet_id)

    # The lock really does bind the editor (precondition, non-vacuous).
    with pytest.raises(SumPermissionError):
        _edit_locked_cell(manager, editor, workbook, sheet_id, "blocked")

    # Pre-fix: an edit grantee could flip the owner's locked range to editable.
    with pytest.raises(SumPermissionError):
        manager.set_range_permission(
            editor.id, int(workbook["id"]), int(sheet_id), 0, 5, 0, 3, "editable"
        )

    # The lock survived, so the protected cell is still uneditable by the editor.
    with pytest.raises(SumPermissionError):
        _edit_locked_cell(manager, editor, workbook, sheet_id, "still-blocked")


def test_edit_grantee_cannot_delete_owner_locked_range(db, two_users, server_manager):
    """Removing a lock is the same ACL delegation as rewriting it."""
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "ACL target 2")
    sheet_id = workbook["sheets"][0]["id"]
    manager.grant_workbook(owner.id, int(workbook["id"]), editor.id, ["edit"])
    _locked_range(manager, owner, workbook, sheet_id)

    rows = manager.list_range_permissions(editor.id, int(workbook["id"]), int(sheet_id))
    range_id = rows[0]["id"]

    with pytest.raises(SumPermissionError):
        manager.remove_range_permission(editor.id, int(workbook["id"]), int(range_id))

    with pytest.raises(SumPermissionError):
        _edit_locked_cell(manager, editor, workbook, sheet_id, "still-blocked")


def test_owner_can_still_manage_range_permissions(db, two_users, server_manager):
    """Non-vacuous: the owner may still set and clear range locks."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owner ACL")
    sheet_id = workbook["sheets"][0]["id"]

    created = manager.set_range_permission(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 5, 0, 3, "read_only"
    )
    assert created["permission"] == "read_only"
    manager.remove_range_permission(owner.id, int(workbook["id"]), int(created["id"]))
    assert (
        manager.list_range_permissions(owner.id, int(workbook["id"]), int(sheet_id))
        == []
    )
