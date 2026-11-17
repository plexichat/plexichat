"""Regression: formula text must be bounded on the operation write path.

XLSX imports route cell formulas through ``bound_formula_text`` before storing
them, but the primary ``apply_operation`` path used to accept any string for a
``formula`` cell. An oversized or control-character-bearing formula could be
persisted into the workbook blob, copied into audit records and echoed into an
exported spreadsheet. These tests pin the shared boundary at that write path.
"""

import pytest

from src.core.suite.boundaries import MAX_FORMULA_LENGTH, MAX_FORMULA_NESTING_DEPTH
from src.core.suite.sum.manager import SumManager, SumValidationError
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _make_manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _new_workbook(manager, owner):
    workbook = manager.create_workbook(owner.id, "Formula boundary")
    return int(workbook["id"]), int(workbook["sheets"][0]["id"])


def _formula_cell(formula):
    return {"row": 0, "column": 0, "value_type": "formula", "formula": formula}


def test_oversized_formula_rejected_and_no_operation_persisted(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    wb_id, sheet_id = _new_workbook(manager, owner)

    huge = "=" + "1+" * (MAX_FORMULA_LENGTH // 2 + 1)
    with pytest.raises(SumValidationError):
        manager.apply_operation(
            owner.id, wb_id, sheet_id, "huge-formula", "set_cell", 0, [_formula_cell(huge)]
        )

    # The rejected write must advance neither the revision nor the op log.
    assert manager.get_workbook(owner.id, wb_id)["current_revision"] == 0


def test_deeply_nested_formula_rejected(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    wb_id, sheet_id = _new_workbook(manager, owner)

    nested = "(" * (MAX_FORMULA_NESTING_DEPTH + 1)
    with pytest.raises(SumValidationError):
        manager.apply_operation(
            owner.id,
            wb_id,
            sheet_id,
            "deep-formula",
            "set_cell",
            0,
            [_formula_cell(nested)],
        )


def test_control_character_formula_rejected(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    wb_id, sheet_id = _new_workbook(manager, owner)

    # A bare CR can be treated as a line delimiter by spreadsheet consumers,
    # turning the tail into a forged attribute/formula line on export.
    injected = "SUM(A1:A2)\r\na=forged:payload"
    with pytest.raises(SumValidationError):
        manager.apply_operation(
            owner.id,
            wb_id,
            sheet_id,
            "cr-formula",
            "set_cell",
            0,
            [_formula_cell(injected)],
        )


def test_non_string_formula_rejected(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    wb_id, sheet_id = _new_workbook(manager, owner)

    with pytest.raises(SumValidationError):
        manager.apply_operation(
            owner.id,
            wb_id,
            sheet_id,
            "non-string-formula",
            "set_cell",
            0,
            [_formula_cell({"not": "a string"})],
        )


def test_valid_formula_still_writes_and_computes(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    wb_id, sheet_id = _new_workbook(manager, owner)

    manager.apply_operation(
        owner.id,
        wb_id,
        sheet_id,
        "seed-value",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "number", "value": 10}],
    )
    result = manager.apply_operation(
        owner.id,
        wb_id,
        sheet_id,
        "valid-formula",
        "set_cell",
        1,
        [{"row": 0, "column": 1, "value_type": "formula", "formula": "A1*2"}],
    )
    assert result["revision"] == 2

    viewport = manager.read_viewport(owner.id, wb_id, sheet_id, 0, 1, 0, 2)
    cells = {f"{cell['row']}:{cell['column']}": cell for cell in viewport["cells"]}
    assert cells["0:1"]["formula"] == "A1*2"
    assert cells["0:1"]["computed"] == 20
