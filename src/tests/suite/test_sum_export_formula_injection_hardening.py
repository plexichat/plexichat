"""Regression hardening for PlexiSum exports and named ranges.

Two independent weaknesses are pinned here:

* Text cells (and string formula results) whose text starts with a spreadsheet
  formula prefix were written verbatim into XLSX/CSV exports, so opening the
  export could execute attacker-chosen text as a live formula.
* Named-range definitions were followed recursively by ``parse_cell_ref`` with
  no cycle guard, so a self-reference or a cycle raised ``RecursionError`` --
  which the evaluator does not catch -- and could crash a viewport/export read.
"""

import base64
import io

import pytest

from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _workbook(manager, owner):
    workbook = manager.create_workbook(owner.id, "Export hardening")
    return int(workbook["id"]), int(workbook["sheets"][0]["id"])


def _write(manager, owner, wb_id, sheet_id, cells, op_id):
    manager.apply_operation(
        owner.id,
        wb_id,
        sheet_id,
        op_id,
        "set_cell",
        int(manager.get_workbook(owner.id, wb_id)["current_revision"]),
        cells,
    )


def test_xlsx_export_keeps_formula_like_text_as_text(db, two_users, server_manager):
    pytest.importorskip("openpyxl")
    import openpyxl

    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)

    payload = '=HYPERLINK("http://evil.example","click")'
    _write(
        manager,
        owner,
        wb_id,
        sheet_id,
        [
            {"row": 0, "column": 0, "value_type": "text", "value": payload},
            {"row": 1, "column": 0, "value_type": "number", "value": 7},
            {"row": 2, "column": 0, "value_type": "formula", "formula": "1+2"},
        ],
        "seed-formula-like-text",
    )

    result = manager.export_xlsx(owner.id, wb_id, sheet_id)
    sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["content"]))).active

    # The text cell must stay literal text, not become an executable formula.
    assert sheet["A1"].data_type == "s"
    assert sheet["A1"].value == payload
    # Genuine numeric and formula cells keep their original semantics.
    assert sheet["A2"].data_type == "n"
    assert sheet["A2"].value == 7
    assert sheet["A3"].data_type == "f"
    assert sheet["A3"].value == "=1+2"


def test_csv_export_quotes_formula_like_text_but_not_numbers(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)

    _write(
        manager,
        owner,
        wb_id,
        sheet_id,
        [
            {"row": 0, "column": 0, "value_type": "text", "value": "=1+1"},
            {"row": 1, "column": 0, "value_type": "text", "value": "+cmd"},
            {"row": 2, "column": 0, "value_type": "text", "value": "@SUM(1)"},
            {"row": 3, "column": 0, "value_type": "number", "value": -5},
        ],
        "seed-csv-injection",
    )

    lines = manager.export_csv(owner.id, wb_id, sheet_id)["content"].splitlines()
    assert lines[0] == "'=1+1"
    assert lines[1] == "'+cmd"
    assert lines[2] == "'@SUM(1)"
    # A negative number is data, not a formula prefix; it must stay numeric.
    assert lines[3] == "-5"


def _resolver(sheet, row, column):
    if (row, column) == (0, 0):
        return {"value_type": "number", "value": 1}
    return None


def test_cyclic_named_ranges_answer_ref_instead_of_recursing():
    from src.core.suite.sum.formulas import FormulaEvaluator, compute_value

    for named_ranges in (
        {"A": "B", "B": "A"},
        {"LOOP": "LOOP"},
        {"A": "B", "B": "C", "C": "A"},
    ):
        evaluator = FormulaEvaluator(_resolver, named_ranges=named_ranges)
        for name in named_ranges:
            assert evaluator.evaluate_cell(None, 5, 5, name) == "#REF!"
        # The public helper takes the same guarded construction path.
        assert (
            compute_value(
                "formula", None, next(iter(named_ranges)), _resolver,
                named_ranges=named_ranges,
            )
            == "#REF!"
        )


def test_valid_named_range_still_resolves():
    from src.core.suite.sum.formulas import FormulaEvaluator

    evaluator = FormulaEvaluator(_resolver, named_ranges={"SALES": "A1"})
    assert evaluator.evaluate_cell(None, 5, 5, "SALES") == 1
