"""CSV export must never emit a file that CSV import refuses to decode.

Follow-up to N738 (``13e488f7``), which closed the same asymmetry for XLSX.
``export_csv`` renders cell values into a text blob; ``import_csv`` bounds the
decoded text at ``MAX_CSV_IMPORT_BYTES`` (2 MB) before it parses any row.

Those two sides were asymmetric. Every write bound was respected -- the rows
and columns were inside ``MAX_CSV_ROWS``/``MAX_CSV_COLUMNS``, each cell was
inside ``MAX_CELL_VALUE_LENGTH`` (100,000 chars), and the raw SQLite blob was
far below the 64 MB container ceiling -- yet a couple of dozen bounded
high-entropy text cells render to a >2 MB CSV. The export succeeded and
returned text the very next ``import_csv`` refused with "Import payload
exceeds ... bytes", breaking copy/backup/restore round trips with a size-cap
failure long after the write was acknowledged.

The fix applies the decode-side byte cap on the encode side (fail closed): an
export that could not be re-imported is refused before it is handed out.
"""

import os

import pytest

from src.core.suite.boundaries import MAX_CELL_VALUE_LENGTH, MAX_CSV_IMPORT_BYTES
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum._shared import SumValidationError

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _workbook(manager, owner, title="csv cap symmetry"):
    wb = manager.create_workbook(owner.id, title)
    return int(wb["id"]), int(wb["sheets"][0]["id"])


def _large_sheet(db, two_users, server_manager):
    """A sheet whose legal cells render past the CSV import byte ceiling."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    # 24 cells x 100000 chars = 2.4 MB of CSV, over the 2 MB import ceiling.
    # Each value is exactly MAX_CELL_VALUE_LENGTH -- inside the write cap -- and
    # the cell count is inside MAX_OPERATION_CELLS.
    value = os.urandom(MAX_CELL_VALUE_LENGTH // 2).hex()[:MAX_CELL_VALUE_LENGTH]
    cells = [
        {"row": 0, "column": c, "value": value, "value_type": "text"} for c in range(24)
    ]
    assert all(len(c["value"]) <= MAX_CELL_VALUE_LENGTH for c in cells)
    rev = int(manager.get_workbook(owner.id, wb_id)["current_revision"])
    manager.apply_operation(owner.id, wb_id, sheet_id, "op-big", "set_cell", rev, cells)
    return manager, owner, wb_id, sheet_id


def test_import_refuses_an_oversized_csv(db, two_users, server_manager):
    """Read side really enforces the byte ceiling (the asymmetry's other half)."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)

    content = "x" * (MAX_CSV_IMPORT_BYTES + 1)
    assert len(content.encode("utf-8")) > MAX_CSV_IMPORT_BYTES  # non-vacuous
    with pytest.raises(SumValidationError, match="exceeds"):
        manager.import_csv(owner.id, wb_id, sheet_id, content)


def test_export_refuses_to_emit_an_unimportable_csv(db, two_users, server_manager):
    """The write path fails closed instead of handing out an unreadable blob."""
    manager, owner, wb_id, sheet_id = _large_sheet(db, two_users, server_manager)

    # Non-vacuous: the write succeeded with fully legal cells, so a faithful
    # export exceeds the import ceiling.
    rev = int(manager.get_workbook(owner.id, wb_id)["current_revision"])
    assert rev > 0
    from src.core.suite.sum.container import MAX_DECOMPRESSED_SIZE, decode_container

    blob = manager._file_bytes(wb_id)
    db_bytes, _, _ = decode_container(blob)
    assert len(db_bytes) <= MAX_DECOMPRESSED_SIZE  # write was inside container cap

    with pytest.raises(SumValidationError, match="exceeds the import size limit"):
        manager.export_csv(owner.id, wb_id, sheet_id)


def test_ordinary_export_still_round_trips(db, two_users, server_manager):
    """Control: a legitimate bounded export remains importable."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    src_id, src_sheet = _workbook(manager, owner, "source")
    rev = int(manager.get_workbook(owner.id, src_id)["current_revision"])
    manager.apply_operation(
        owner.id,
        src_id,
        src_sheet,
        "op-small",
        "set_cell",
        rev,
        [
            {"row": r, "column": c, "value": f"r{r}c{c}", "value_type": "text"}
            for r in range(3)
            for c in range(3)
        ],
    )
    exported = manager.export_csv(owner.id, src_id, src_sheet)
    assert len(exported["content"].encode("utf-8")) <= MAX_CSV_IMPORT_BYTES

    dst_id, dst_sheet = _workbook(manager, owner, "copy")
    dst_rev = int(manager.get_workbook(owner.id, dst_id)["current_revision"])
    result = manager.import_csv(
        owner.id, dst_id, dst_sheet, exported["content"], base_revision=dst_rev
    )
    assert result["cells"] == 9
    viewport = manager.read_viewport(owner.id, dst_id, dst_sheet, 0, 3, 0, 3)
    values = {cell["value"] for cell in viewport["cells"]}
    assert {"r0c0", "r1c1", "r2c2"} <= values
