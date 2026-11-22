"""Whole-file replacement must not rewrite cells the owner locked read-only.

``apply_operation``, ``import_csv``/``import_xlsx`` and the structural edit
path all funnel cell writes through ``_check_range_permissions`` (or its
structural twin) before mutating. ``upload_file`` publishes an arbitrary
``.plexisum`` SQLite blob straight to the revision store, so an ``edit``
grantee -- who passes ``_workbook(..., mutation=True)`` -- could download the
workbook, rewrite a protected cell in the file, and upload it back. The ACL
lives in the main database, not in the uploaded blob, so no other gate noticed
the overwrite.

Regression guard: the forged upload is refused, while an editor's upload that
only touches unprotected cells and the owner's upload that touches protected
cells still succeed (the check is not vacuous).
"""

import json

import pytest

from src.core.suite.sum.manager import SumManager, SumPermissionError
from src.core.suite.sum.schema import create_tables as create_sum_tables

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _set_cell(manager, user, wb_id, sheet_id, op_id, row, column, value):
    revision = int(manager._workbook(user.id, wb_id)["current_revision"])
    return manager.apply_operation(
        user.id,
        wb_id,
        sheet_id,
        op_id,
        "set_cell",
        revision,
        [{"row": row, "column": column, "value_type": "text", "value": value}],
    )


def _cell_value(manager, wb_id, sheet_id, row, column):
    connection, path = manager._open_sqlite(manager._file_bytes(wb_id))
    try:
        row_result = connection.execute(
            "SELECT value_json FROM sum_cells WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
            (str(sheet_id), row, column),
        ).fetchone()
        return json.loads(row_result["value_json"]) if row_result else None
    finally:
        manager._close_sqlite(connection, path)


def _forged_file(manager, wb_id, sheet_id, row, column, value):
    """Take the current file bytes and rewrite one cell directly in SQLite."""
    connection, path = manager._open_sqlite(manager._file_bytes(wb_id))
    try:
        connection.execute(
            "UPDATE sum_cells SET value_type = 'text', value_json = ? "
            "WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
            (json.dumps(value), str(sheet_id), row, column),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
        with open(path, "rb") as file_handle:
            return file_handle.read()
    finally:
        manager._close_sqlite(connection, path)


def test_edit_grantee_cannot_upload_file_rewriting_read_only_cells(
    db, two_users, server_manager
):
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Protected upload")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    _set_cell(manager, owner, wb_id, sheet_id, "seed-locked", 0, 0, "locked")
    _set_cell(manager, owner, wb_id, sheet_id, "seed-free", 10, 0, "free")
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")

    # Non-vacuous precondition: the ordinary cell-write API really does bind
    # the editor inside the band, and the editor really can write outside it.
    with pytest.raises(SumPermissionError):
        _set_cell(manager, editor, wb_id, sheet_id, "op-blocked", 0, 0, "blocked")
    _set_cell(manager, editor, wb_id, sheet_id, "op-allowed", 10, 0, "editor-ok")
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) == "locked"

    forged = _forged_file(manager, wb_id, sheet_id, 0, 0, "pwned")

    # Before the fix this succeeded and silently replaced the protected cell.
    with pytest.raises(SumPermissionError):
        manager.upload_file(editor.id, wb_id, forged)

    assert _cell_value(manager, wb_id, sheet_id, 0, 0) == "locked"


def test_upload_file_still_allows_editor_changes_outside_protected_range(
    db, two_users, server_manager
):
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Protected upload OK")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    _set_cell(manager, owner, wb_id, sheet_id, "seed-locked", 0, 0, "locked")
    _set_cell(manager, owner, wb_id, sheet_id, "seed-free", 10, 0, "free")
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")

    allowed = _forged_file(manager, wb_id, sheet_id, 10, 0, "uploaded-by-editor")
    revision = int(manager._workbook(editor.id, wb_id)["current_revision"])
    result = manager.upload_file(editor.id, wb_id, allowed, revision)

    assert result["revision"] == revision + 1
    assert _cell_value(manager, wb_id, sheet_id, 10, 0) == "uploaded-by-editor"
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) == "locked"


def test_edit_grantee_cannot_restore_snapshot_rewriting_read_only_cells(
    db, two_users, server_manager
):
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Protected restore")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    _set_cell(manager, owner, wb_id, sheet_id, "seed-locked", 0, 0, "before-lock")
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    snapshot = manager.create_snapshot(editor.id, wb_id)

    # The owner changes the cell and then locks its band.
    _set_cell(manager, owner, wb_id, sheet_id, "owner-edit", 0, 0, "after-lock")
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) == "after-lock"

    # Before the fix, restoring the editor's older snapshot rolled the locked
    # cell back to "before-lock" without any protected-range check.
    with pytest.raises(SumPermissionError):
        manager.restore_snapshot(editor.id, wb_id, int(snapshot["id"]))

    assert _cell_value(manager, wb_id, sheet_id, 0, 0) == "after-lock"


def test_owner_can_still_restore_snapshot_across_protected_range(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owner restore")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    _set_cell(manager, owner, wb_id, sheet_id, "seed-locked", 0, 0, "before-lock")
    snapshot = manager.create_snapshot(owner.id, wb_id)
    _set_cell(manager, owner, wb_id, sheet_id, "owner-edit", 0, 0, "after-lock")
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")

    restored = manager.restore_snapshot(owner.id, wb_id, int(snapshot["id"]))

    assert restored["current_revision"] >= 2
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) == "before-lock"


def test_owner_can_still_upload_changes_inside_protected_range(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owner upload")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    _set_cell(manager, owner, wb_id, sheet_id, "seed-locked", 0, 0, "locked")
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")

    allowed = _forged_file(manager, wb_id, sheet_id, 0, 0, "owner-override")
    result = manager.upload_file(owner.id, wb_id, allowed)

    assert result["revision"] >= 2
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) == "owner-override"
