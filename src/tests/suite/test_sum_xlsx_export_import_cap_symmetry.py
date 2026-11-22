"""XLSX export must never emit a file that XLSX import refuses to decode.

The storage container is zstd-compressed on write and decompressed under a
hard ceiling (``MAX_DECOMPRESSED_SIZE``) on every read -- N686 (``906e3976``)
closed that asymmetry for the container write paths.  The XLSX codec has the
same shape: ``export_xlsx`` encodes workbook cells into a ZIP file, and
``import_xlsx`` decodes it under ``MAX_XLSX_IMPORT_BYTES`` plus the ZIP
central-directory limits in ``_reject_zip_bomb``.

Those two sides were asymmetric.  Every write bound was respected -- the raw
SQLite blob stayed under the 64 MB container ceiling, and each cell value was
inside ``MAX_CELL_VALUE_LENGTH`` -- yet 2000 bounded high-entropy text cells
render to a ~23 MB XLSX, over the 20 MB import ceiling.  The export succeeded
and returned a file the very next ``import_xlsx`` refused with "Import payload
exceeds ... bytes", breaking copy/backup/restore round trips with a size-cap
failure long after the write was acknowledged.

The fix applies the decode-side caps on the encode side (fail closed): an
export that could not be re-imported is refused before it is handed out.
"""

import base64
import os

import pytest

from src.core.suite.boundaries import MAX_XLSX_IMPORT_BYTES
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum._shared import SumValidationError

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _workbook(manager, owner, title="xlsx cap symmetry"):
    wb = manager.create_workbook(owner.id, title)
    return int(wb["id"]), int(wb["sheets"][0]["id"])


def _large_workbook(db, two_users, server_manager):
    """A workbook whose legitimate cells render past the XLSX import ceiling."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    # 2000 cells (MAX_OPERATION_CELLS) x 15000 high-entropy chars each, well
    # inside MAX_CELL_VALUE_LENGTH. 30 MB of cell text fits in the 64 MB raw
    # SQLite blob but produces a ~23 MB XLSX (> 20 MB import ceiling).
    cells = [
        {
            "row": r,
            "column": c,
            "value": base64.b64encode(os.urandom(11250)).decode("ascii"),
            "value_type": "text",
        }
        for r in range(40)
        for c in range(50)
    ]
    rev = int(manager.get_workbook(owner.id, wb_id)["current_revision"])
    manager.apply_operation(owner.id, wb_id, sheet_id, "op-big", "set_cell", rev, cells)
    return manager, owner, wb_id, sheet_id, cells


def test_import_refuses_an_oversized_xlsx(db, two_users, server_manager):
    """Read side really enforces the ceiling (the asymmetry's other half)."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)

    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    # Unique high-entropy values per row so the deflated XLSX really clears
    # MAX_XLSX_IMPORT_BYTES (a repeated value compresses to almost nothing).
    for row in range(2000):
        ws.cell(
            row=row + 1,
            column=1,
            value=base64.b64encode(os.urandom(12000)).decode("ascii"),
        )
    buffer = io.BytesIO()
    wb.save(buffer)
    assert len(buffer.getvalue()) > MAX_XLSX_IMPORT_BYTES  # non-vacuous
    content = base64.b64encode(buffer.getvalue()).decode("ascii")

    with pytest.raises(SumValidationError, match="exceeds"):
        manager.import_xlsx(
            owner.id,
            wb_id,
            sheet_id,
            content,
            base_revision=int(
                manager.get_workbook(owner.id, wb_id)["current_revision"]
            ),
        )


def test_export_refuses_to_emit_an_unimportable_xlsx(db, two_users, server_manager):
    """The write path fails closed instead of handing out an unreadable file."""
    manager, owner, wb_id, _, cells = _large_workbook(db, two_users, server_manager)

    # Non-vacuous: the write succeeded and the legitimate cell payload alone
    # is larger than the import ceiling, so a faithful export must exceed it.
    assert sum(len(c["value"]) for c in cells) > MAX_XLSX_IMPORT_BYTES
    from src.core.suite.sum.container import (
        MAX_DECOMPRESSED_SIZE,
        decode_container,
    )

    blob = manager._file_bytes(wb_id)
    db_bytes, _, _ = decode_container(blob)
    assert len(db_bytes) <= MAX_DECOMPRESSED_SIZE  # write was inside container cap

    with pytest.raises(SumValidationError, match="exceeds the import size limit"):
        manager.export_xlsx(owner.id, wb_id)


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
    exported = manager.export_xlsx(owner.id, src_id)

    dst_id, dst_sheet = _workbook(manager, owner, "copy")
    dst_rev = int(manager.get_workbook(owner.id, dst_id)["current_revision"])
    result = manager.import_xlsx(
        owner.id,
        dst_id,
        dst_sheet,
        exported["content"],
        base_revision=dst_rev,
    )
    assert result["cells"] == 9
    viewport = manager.read_viewport(owner.id, dst_id, dst_sheet, 0, 3, 0, 3)
    values = {cell["value"] for cell in viewport["cells"]}
    assert {"r0c0", "r1c1", "r2c2"} <= values
