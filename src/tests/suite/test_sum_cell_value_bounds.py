"""Regression tests for bounded PlexiSum cell values.

A single cell's value is copied into the workbook blob, the durable operation
log, audit payloads and CSV/XLSX exports. Formula text was already bounded, but
free-form text values had no ceiling of their own, so one operation could
persist an arbitrarily large string that is reloaded on every read and
re-encoded on every export (storage/CPU DoS). Both the direct operation path and
the chunked import path must enforce the same ceiling.
"""

import pytest

from src.core.suite.boundaries import MAX_CELL_VALUE_LENGTH
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum._shared import SumValidationError


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _workbook(manager, owner):
    workbook = manager.create_workbook(owner.id, "Cell value bounds")
    return int(workbook["id"]), int(workbook["sheets"][0]["id"])


def test_operation_rejects_oversized_text_value(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    revision = int(manager.get_workbook(owner.id, wb_id)["current_revision"])

    with pytest.raises(SumValidationError):
        manager.apply_operation(
            owner.id,
            wb_id,
            sheet_id,
            "oversized-cell",
            "set_cell",
            revision,
            [
                {
                    "row": 0,
                    "column": 0,
                    "value_type": "text",
                    "value": "x" * (MAX_CELL_VALUE_LENGTH + 1),
                }
            ],
        )


def test_operation_accepts_large_but_bounded_value(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    revision = int(manager.get_workbook(owner.id, wb_id)["current_revision"])
    value = "y" * MAX_CELL_VALUE_LENGTH

    result = manager.apply_operation(
        owner.id,
        wb_id,
        sheet_id,
        "bounded-cell",
        "set_cell",
        revision,
        [{"row": 0, "column": 0, "value_type": "text", "value": value}],
    )
    assert result["replayed"] is False


def test_chunked_import_rejects_oversized_value(db, two_users, server_manager):
    """The bulk path builds normalized cells itself; it must not bypass the bound."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    revision = int(manager.get_workbook(owner.id, wb_id)["current_revision"])

    big = "z" * (MAX_CELL_VALUE_LENGTH + 1)
    # More than MAX_OPERATION_CELLS (2000) cells forces the chunked bulk path.
    content = "\n".join([big] + ["small"] * 2000)

    with pytest.raises(SumValidationError):
        manager.import_csv(owner.id, wb_id, sheet_id, content, base_revision=revision)


def test_bound_cell_value_helper_only_checks_strings():
    from src.core.suite.boundaries import bound_cell_value

    assert bound_cell_value(12345) == 12345
    assert bound_cell_value(None) is None
    assert bound_cell_value(True) is True
    assert bound_cell_value("a" * MAX_CELL_VALUE_LENGTH) == "a" * MAX_CELL_VALUE_LENGTH
    with pytest.raises(ValueError):
        bound_cell_value("a" * (MAX_CELL_VALUE_LENGTH + 1), error_cls=ValueError)
