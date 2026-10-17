"""Regression tests for the session-path viewport segment merge (Agent 30 H1).

A hot column that crossed the promotion threshold gets packed into
``sum_column_segments``; session-path viewport reads must merge those packed
cells with the sparse ``sum_cells`` rows without crashing and with sparse
values taking precedence.
"""

import json

import pytest

from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum.segments import SegmentEncoder


@pytest.fixture()
def manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _make_workbook(manager, owner):
    workbook = manager.create_workbook(owner.id, "Segment viewport")
    sheet_id = int(workbook["sheets"][0]["id"])
    return workbook, sheet_id


def _insert_segment(manager, owner, workbook, sheet_id, cells):
    """Pack ``cells`` into a column segment inside the live session copy."""
    segments = SegmentEncoder.pack_column(cells)
    assert segments, "expected at least one packed segment"
    with manager._session_lock(int(workbook["id"])):
        connection = manager._materialized_connection_unlocked(int(workbook["id"]))
        for seg_id, seg_data in segments:
            decoded = SegmentEncoder.decode_segment(seg_data)
            connection.execute(
                "INSERT OR REPLACE INTO sum_column_segments "
                "(sheet_id, column_id, segment_id, row_start, row_end, "
                "encoding, compressed_values, statistics_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(sheet_id),
                    "0",
                    seg_id,
                    decoded["row_start"],
                    decoded["row_end"],
                    decoded["encoding"],
                    seg_data,
                    json.dumps(decoded.get("statistics", {})),
                ),
            )
        connection.commit()


def test_viewport_merges_segment_cells_without_crash(manager, two_users):
    """Gap-filled segment cells must appear in the viewport payload."""
    owner, _ = two_users
    workbook, sheet_id = _make_workbook(manager, owner)

    # One sparse cell in the merged range (row 0), one outside it.
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        sheet_id,
        "op-seg-1",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "number", "value": 111}],
    )

    # Segment covers rows 0..9 in column 0; row 5 exists ONLY in the segment.
    segment_cells = [
        {
            "row_index": r,
            "value_type": "number",
            "value_json": json.dumps(float(500 + r)),
            "formula": None,
            "style_json": None,
        }
        for r in range(10)
    ]
    _insert_segment(manager, owner, workbook, sheet_id, segment_cells)

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), sheet_id, 0, 10, 0, 1
    )
    by_position = {(cell["row"], cell["column"]): cell for cell in viewport["cells"]}
    # Row 5 comes from the segment (gap fill) — this used to raise
    # TypeError: 'Row' object is not subscriptable -> HTTP 500.
    assert (5, 0) in by_position
    assert by_position[(5, 0)]["value"] == 505


def test_sparse_cell_wins_over_segment_value(manager, two_users):
    """Sparse sum_cells values take precedence over packed segment values."""
    owner, _ = two_users
    workbook, sheet_id = _make_workbook(manager, owner)

    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        sheet_id,
        "op-seg-2",
        "set_cell",
        0,
        [{"row": 2, "column": 0, "value_type": "number", "value": 42}],
    )
    segment_cells = [
        {
            "row_index": 2,
            "value_type": "number",
            "value_json": json.dumps(9999.0),
            "formula": None,
            "style_json": None,
        }
    ]
    _insert_segment(manager, owner, workbook, sheet_id, segment_cells)

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), sheet_id, 0, 5, 0, 1
    )
    cell = next(c for c in viewport["cells"] if c["row"] == 2 and c["column"] == 0)
    assert cell["value"] == 42
