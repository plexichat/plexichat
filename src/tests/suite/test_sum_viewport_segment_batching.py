"""Regression tests for batched segment hydration on the session viewport path.

``read_viewport`` used to issue one ``sum_column_segments`` SELECT per column in
the requested range -- an N+1 on a hot poll path.  These tests pin three
properties of the bounded-IN rewrite:

* **parity** -- gap-filled segment cells still appear and sparse cells still
  win, exactly as before;
* **query-count reduction / bounds** -- a wide column range costs
  ``ceil(columns / 500)`` queries, each binding at most 500 ids, instead of one
  query per column (measured here against the legacy loop in the same test);
* **no cross-user leak** -- the batch stays scoped to the authorized sheet and
  cannot pull a segment out of another user's workbook.
"""

import json
import math
from contextlib import contextmanager

import pytest

from src.core.suite.sum._shared import _WORKBOOK_SESSIONS
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum.segments import SegmentEncoder


@pytest.fixture()
def manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


class _RecordingConnection:
    """Delegating sqlite connection that records ``(sql, param_count)``."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, len(params) if params is not None else 0))
        return self._inner.execute(sql, params)

    def executemany(self, sql, seq):
        return self._inner.executemany(sql, seq)

    def commit(self):
        return self._inner.commit()

    def rollback(self):
        return self._inner.rollback()


@contextmanager
def _recording(manager, workbook_id):
    """Temporarily wrap the live session connection to capture its queries."""
    session = _WORKBOOK_SESSIONS[int(workbook_id)]
    real = session.connection
    recorder = _RecordingConnection(real)
    session.connection = recorder
    try:
        yield recorder
    finally:
        session.connection = real


def _segment_calls(recorder):
    return [(sql, n) for sql, n in recorder.calls if "sum_column_segments" in sql]


def _make_workbook(manager, owner, name="Batched viewport"):
    workbook = manager.create_workbook(owner.id, name)
    sheet_id = int(workbook["sheets"][0]["id"])
    return workbook, sheet_id


def _cells_for_column(col, rows=(0, 1, 2, 3, 4, 5), offset=0):
    return [
        {
            "row_index": r,
            "value_type": "number",
            "value_json": json.dumps(float(offset + 1000 * col + r)),
            "formula": None,
            "style_json": None,
        }
        for r in rows
    ]


def _insert_segments(manager, workbook, sheet_id, columns, offset=0):
    """Pack one segment per requested column into the live session connection."""
    rows = []
    for col in columns:
        for seg_id, seg_data in SegmentEncoder.pack_column(
            _cells_for_column(col, offset=offset)
        ):
            decoded = SegmentEncoder.decode_segment(seg_data)
            rows.append(
                (
                    str(sheet_id),
                    str(col),
                    seg_id,
                    decoded["row_start"],
                    decoded["row_end"],
                    decoded["encoding"],
                    seg_data,
                    json.dumps(decoded.get("statistics", {})),
                )
            )
    with manager._session_lock(int(workbook["id"])):
        connection = manager._materialized_connection_unlocked(int(workbook["id"]))
        connection.executemany(
            "INSERT OR REPLACE INTO sum_column_segments "
            "(sheet_id, column_id, segment_id, row_start, row_end, "
            "encoding, compressed_values, statistics_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()


def _legacy_loaded_cells(
    connection, sheet_id, row_start, row_end, column_start, column_end
):
    """The pre-rewrite per-column loop, used to measure the 'before' count."""
    found = {}
    for col in range(column_start, column_end):
        seg_rows = connection.execute(
            "SELECT segment_id, compressed_values FROM sum_column_segments "
            "WHERE sheet_id = ? AND column_id = ? AND row_end > ? AND row_start < ?",
            (str(sheet_id), str(col), row_start, row_end),
        ).fetchall()
        for seg_row in seg_rows:
            decoded = SegmentEncoder.decode_segment(seg_row["compressed_values"])
            for cell in decoded.get("cells", []) or []:
                if not isinstance(cell, dict):
                    continue
                r = int(cell.get("row_index", cell.get("row", 0)))
                if row_start <= r < row_end:
                    found[(r, col)] = cell
    return found


def test_wide_viewport_batches_segment_reads_with_parity(manager, two_users):
    """Batch 600 columns into <=500-id queries and keep the merged cells."""
    owner, _ = two_users
    workbook, sheet_id = _make_workbook(manager, owner)
    columns = list(range(600))
    # A sparse cell inside the range must still take precedence over a segment.
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        sheet_id,
        "op-batched-1",
        "set_cell",
        0,
        [{"row": 3, "column": 7, "value_type": "number", "value": 42}],
    )
    _insert_segments(manager, workbook, sheet_id, columns)

    row_start, row_end = 0, 6
    col_start, col_end = 0, 600

    # Measure the legacy per-column loop under the same recording wrapper.
    with _recording(manager, workbook["id"]) as before_rec:
        _legacy_loaded_cells(
            before_rec, sheet_id, row_start, row_end, col_start, col_end
        )
    before = _segment_calls(before_rec)

    with _recording(manager, workbook["id"]) as after_rec:
        viewport = manager.read_viewport(
            owner.id,
            int(workbook["id"]),
            sheet_id,
            row_start,
            row_end,
            col_start,
            col_end,
        )
    after = _segment_calls(after_rec)

    # Before: one 4-param query per column. After: ceil(600/500) == 2 queries.
    assert len(before) == len(columns) == 600
    assert all(n == 4 for _, n in before)
    assert math.ceil(len(columns) / 500) == 2
    assert len(after) == 2
    assert len(after) < len(before)
    # Each batched SELECT binds 3 fixed predicates + at most 500 ids.
    assert all(n <= 3 + 500 for _, n in after)
    assert max(n - 3 for _, n in after) <= 500

    by_position = {(cell["row"], cell["column"]): cell for cell in viewport["cells"]}
    # Parity: every packed column is gap-filled, including the last one in the
    # second chunk (column 599), which the per-column loop also returned.
    assert by_position[(5, 0)]["value"] == 1000 * 0 + 5
    assert by_position[(5, 599)]["value"] == 1000 * 599 + 5
    # Sparse cell still wins over its segment neighbour.
    assert by_position[(3, 7)]["value"] == 42


def test_narrow_viewport_uses_single_batched_query(manager, two_users):
    """A short column range costs exactly one batched SELECT, not one per column."""
    owner, _ = two_users
    workbook, sheet_id = _make_workbook(manager, owner)
    columns = list(range(8))
    _insert_segments(manager, workbook, sheet_id, columns)

    with _recording(manager, workbook["id"]) as rec:
        viewport = manager.read_viewport(
            owner.id, int(workbook["id"]), sheet_id, 0, 6, 0, 8
        )
    after = _segment_calls(rec)

    assert len(after) == 1
    assert after[0][1] == 3 + 8
    by_position = {(c["row"], c["column"]): c for c in viewport["cells"]}
    for col in columns:
        assert by_position[(4, col)]["value"] == 1000 * col + 4


def test_batched_segments_do_not_leak_other_users_sheet(manager, two_users):
    """The IN batch stays inside the authorized sheet, not another workbook."""
    owner, stranger = two_users
    workbook, sheet_id = _make_workbook(manager, owner, "Victim")
    other_wb, other_sheet = _make_workbook(manager, stranger, "Stranger")
    _insert_segments(manager, workbook, sheet_id, [0, 1, 2])
    # A distinctive value in a different user's workbook at the same column ids.
    _insert_segments(manager, other_wb, other_sheet, [0, 1, 2], offset=1_000_000)

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), sheet_id, 0, 6, 0, 3
    )
    by_position = {(c["row"], c["column"]): c for c in viewport["cells"]}
    assert len([c for c in by_position if c[1] in (0, 1, 2) and c[0] == 5]) == 3
    assert by_position[(5, 1)]["value"] == 1000 * 1 + 5
    # None of the stranger's distinctive values may cross the sheet boundary.
    assert all(cell["value"] < 1_000_000 for cell in viewport["cells"]), (
        "segment from another user's workbook leaked into the viewport"
    )
