"""sort_range must enforce read_only range permissions (N788).

``sort_range`` deletes and re-inserts every cell in the affected row band.  An
``edit`` grantee who can call it could otherwise reorder cells inside a
``read_only`` protected range, rewriting the protected coordinates' values --
an ACL enforcement bypass: that same editor is blocked from editing any of
those cells through ``apply_operation``/``set_cell``.

These tests prove the editor cannot launder ``sort_range`` into a write into a
protected band (including a band whose protected coordinates start out empty),
while the owner -- who is allowed to sort -- still can, so the guard is not
vacuous.
"""

import pytest

from src.core.suite.sum.manager import SumManager, SumPermissionError
from src.core.suite.sum.schema import create_tables as create_sum_tables

pytestmark = pytest.mark.unit


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _seed(manager, owner, wb_id, sheet_id, op_id, cells):
    manager.apply_operation(
        owner.id, wb_id, sheet_id, op_id, "set_cell", 0, cells
    )


def test_edit_grantee_cannot_sort_through_read_only_range(
    db, two_users, server_manager
):
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Sort ACL target")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    # Distinct values so a sort visibly rewrites the protected band.
    _seed(
        manager,
        owner,
        wb_id,
        sheet_id,
        "seed-1",
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 30},
            {"row": 1, "column": 0, "value_type": "number", "value": 10},
            {"row": 2, "column": 0, "value_type": "number", "value": 20},
        ],
    )
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 3, 0, 1, "read_only")

    # Non-vacuous precondition: the direct write path really does bind editor.
    with pytest.raises(SumPermissionError):
        manager.apply_operation(
            editor.id,
            wb_id,
            sheet_id,
            "editor-direct",
            "set_cell",
            1,
            [{"row": 0, "column": 0, "value_type": "number", "value": 999}],
        )

    # Pre-fix: sort_range skipped _check_range_permissions, so the editor could
    # reorder the band -- (0,0)=30 becomes (0,0)=10 -- rewriting a protected cell.
    with pytest.raises(SumPermissionError):
        manager.sort_range(
            editor.id,
            wb_id,
            sheet_id,
            column=0,
            start_row=0,
            end_row=3,
            direction="asc",
            base_revision=1,
        )

    # The protected cell is unchanged after the blocked sort.
    viewport = manager.read_viewport(owner.id, wb_id, sheet_id, 0, 1, 0, 1)
    assert [c["value"] for c in viewport["cells"] if c["row"] == 0] == [30]


def test_edit_grantee_cannot_sort_into_empty_protected_row(
    db, two_users, server_manager
):
    """The band can receive a value in a protected coordinate that was empty."""
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Sort into protected")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    # Only rows 1..3 hold data; row 0 is empty but protected.
    _seed(
        manager,
        owner,
        wb_id,
        sheet_id,
        "seed-2",
        [
            {"row": 1, "column": 0, "value_type": "number", "value": 30},
            {"row": 2, "column": 0, "value_type": "number", "value": 10},
            {"row": 3, "column": 0, "value_type": "number", "value": 20},
        ],
    )
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 1, 0, 1, "read_only")

    # Sorting rows 0..4 would move a value into the protected (0,0).
    with pytest.raises(SumPermissionError):
        manager.sort_range(
            editor.id,
            wb_id,
            sheet_id,
            column=0,
            start_row=0,
            end_row=4,
            direction="asc",
            base_revision=1,
        )

    viewport = manager.read_viewport(owner.id, wb_id, sheet_id, 0, 2, 0, 1)
    assert [c["value"] for c in viewport["cells"] if c["row"] == 0] == []


def test_owner_can_still_sort_read_only_range(db, two_users, server_manager):
    """Non-vacuous: the owner may sort a band they locked with read_only."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owner sort ACL")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])
    _seed(
        manager,
        owner,
        wb_id,
        sheet_id,
        "seed-3",
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 30},
            {"row": 1, "column": 0, "value_type": "number", "value": 10},
        ],
    )
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 2, 0, 1, "read_only")

    result = manager.sort_range(
        owner.id,
        wb_id,
        sheet_id,
        column=0,
        start_row=0,
        end_row=2,
        direction="asc",
        base_revision=1,
    )
    assert result["rows"] == 2
    viewport = manager.read_viewport(owner.id, wb_id, sheet_id, 0, 2, 0, 1)
    values = [c["value"] for c in sorted(viewport["cells"], key=lambda c: c["row"])]
    assert values == [10, 30]
