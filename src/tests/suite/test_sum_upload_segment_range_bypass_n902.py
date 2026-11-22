"""N902: segment gap-fillers must not bypass whole-file range-permission checks.

``_check_replacement_range_permissions`` compares only ``sum_cells`` rows, but
the session-path viewport also renders ``sum_column_segments`` cells as
gap-fillers for coordinates that have no sparse cell. A forged upload that
leaves ``sum_cells`` untouched yet inserts a segment at an EMPTY coordinate
inside an owner-created ``read_only`` band therefore produces before == after:
no ``SumPermissionError``, and after promotion the viewport renders the
injected value inside the protected band.

These tests prove the injection is real (it renders in the viewport), then
assert the whole-file write paths refuse it. A segment injected outside any
protected band must still be allowed, and owner uploads are unaffected.
"""

import json
import time

import pytest

from src.core.suite.sum.manager import SumManager, SumPermissionError
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum.segments import SegmentEncoder

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _cell_value(manager, wb_id, sheet_id, row, column):
    connection, path = manager._open_sqlite(manager._file_bytes(wb_id))
    try:
        result = connection.execute(
            "SELECT value_json FROM sum_cells "
            "WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
            (str(sheet_id), row, column),
        ).fetchone()
        return json.loads(result["value_json"]) if result else None
    finally:
        manager._close_sqlite(connection, path)


def _forged_segment_file(manager, wb_id, sheet_id, row, column, value):
    """Inject one segment-backed cell at an otherwise empty coordinate.

    ``sum_cells`` is left untouched; only a ``sum_column_segments`` row that
    decodes to a cell at ``(row, column)`` is added to the file bytes.
    """
    cell = {
        "row_index": row,
        "value_type": "text",
        "value_json": json.dumps(value),
        "formula": None,
        "style_json": None,
    }
    seg_data = SegmentEncoder.encode_segment([cell])
    decoded = SegmentEncoder.decode_segment(seg_data)
    connection, path = manager._open_sqlite(manager._file_bytes(wb_id))
    try:
        connection.execute(
            "INSERT OR REPLACE INTO sum_column_segments "
            "(sheet_id, column_id, segment_id, row_start, row_end, "
            "encoding, compressed_values, statistics_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(sheet_id),
                str(column),
                f"seg-{row}-{row + 1}",
                decoded["row_start"],
                decoded["row_end"],
                decoded["encoding"],
                seg_data,
                json.dumps(decoded.get("statistics", {})),
            ),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
        with open(path, "rb") as file_handle:
            return file_handle.read()
    finally:
        manager._close_sqlite(connection, path)


def _publish_and_render(manager, user, wb_id, sheet_id, forged, row, column):
    """Promote ``forged`` directly (bypassing the ACL gate) and read its cell.

    Proves the injected segment is a live, renderable gap-filler in the
    session-path viewport, independent of the range check under test. The live
    session is the only viewport path that merges segments, so force a
    materialization after the promotion.
    """
    base = int(manager._workbook(user.id, wb_id)["current_revision"])
    manager._publish_revision(
        wb_id,
        base,
        base + 1,
        forged,
        int(time.time() * 1000),
        "conflict",
    )
    with manager._session_lock(wb_id):
        manager._materialized_connection_unlocked(wb_id)
    viewport = manager.read_viewport(user.id, wb_id, sheet_id, 0, 5, 0, 20)
    return next(
        (
            cell
            for cell in viewport["cells"]
            if cell["row"] == row and cell["column"] == column
        ),
        None,
    )


def test_forged_segment_cell_renders_in_viewport(db, two_users, server_manager):
    """Non-vacuity: the injected segment really renders in the locked band."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Segment render proof")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    # (0, 0) is intentionally EMPTY in sum_cells, then locked read_only.
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) is None

    forged = _forged_segment_file(manager, wb_id, sheet_id, 0, 0, "segment-pwned")
    rendered = _publish_and_render(manager, owner, wb_id, sheet_id, forged, 0, 0)
    assert rendered is not None and rendered["value"] == "segment-pwned"


def test_edit_grantee_cannot_upload_segment_into_empty_protected_cell(
    db, two_users, server_manager
):
    """A segment injected at an empty protected coordinate is an added cell."""
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Segment ACL bypass")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    # Empty read_only band: (0, 0) has no sum_cells row.
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) is None

    forged = _forged_segment_file(manager, wb_id, sheet_id, 0, 0, "segment-pwned")

    # Before the fix this succeeded silently. The merged-view comparison must
    # now see the segment cell as an addition inside the protected band.
    with pytest.raises(SumPermissionError):
        manager.upload_file(editor.id, wb_id, forged)

    assert _cell_value(manager, wb_id, sheet_id, 0, 0) is None


def test_edit_grantee_cannot_restore_snapshot_with_segment_in_protected_cell(
    db, two_users, server_manager
):
    """restore_snapshot is the sibling whole-file path and must fail closed."""
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Segment restore bypass")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    clean_bytes = manager._file_bytes(wb_id)
    forged = _forged_segment_file(manager, wb_id, sheet_id, 0, 0, "segment-pwned")

    # Publish the forged blob directly so it can be captured in a snapshot.
    base = int(manager._workbook(owner.id, wb_id)["current_revision"])
    manager._publish_revision(
        wb_id, base, base + 1, forged, int(time.time() * 1000), "conflict"
    )
    snapshot = manager.create_snapshot(editor.id, wb_id)

    # Restore the workbook to its clean state, then lock the empty band.
    base = int(manager._workbook(owner.id, wb_id)["current_revision"])
    manager._publish_revision(
        wb_id, base, base + 1, clean_bytes, int(time.time() * 1000), "conflict"
    )
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")
    assert _cell_value(manager, wb_id, sheet_id, 0, 0) is None

    # Restoring the old snapshot reintroduces the segment at (0, 0).
    with pytest.raises(SumPermissionError):
        manager.restore_snapshot(editor.id, wb_id, int(snapshot["id"]))

    assert _cell_value(manager, wb_id, sheet_id, 0, 0) is None


def test_legitimate_segment_outside_protected_band_still_uploads(
    db, two_users, server_manager
):
    """Segment rows outside protected bands must not be blocked."""
    owner, editor = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Segment outside band")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    # Band covers columns 0..2; inject a segment at column 10 (outside it).
    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")
    forged = _forged_segment_file(manager, wb_id, sheet_id, 0, 10, "allowed-segment")

    result = manager.upload_file(editor.id, wb_id, forged)
    assert result["revision"] >= 1


def test_owner_can_upload_segment_inside_protected_band(db, two_users, server_manager):
    """Owner uploads bypass the check, including segment-backed cells."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owner segment upload")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    manager.set_range_permission(owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only")
    forged = _forged_segment_file(manager, wb_id, sheet_id, 0, 0, "owner-segment")

    result = manager.upload_file(owner.id, wb_id, forged)
    assert result["revision"] >= 1
