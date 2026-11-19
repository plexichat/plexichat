"""Regression hardening for unbounded PlexiSum JSON metadata.

Cell styles, data-validation criteria/messages and conditional-format
criteria/styles are free-form JSON objects that are serialized into the
workbook blob, the durable operation log and every read/segment rebuild. Cell
*values* and formulas were already bounded, but these objects were not: a
single edit could persist a multi-megabyte string or a deeply nested tree that
makes json.dumps/json.loads recurse and stalls every subsequent read/export.
The shared ``bound_workspace_json`` boundary must reject both, while ordinary
formatting objects keep working.
"""

import pytest

from src.core.suite.boundaries import (
    MAX_WORKSPACE_JSON_SERIALIZED_LENGTH,
    bound_workspace_json,
)
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.security.hardening import HardeningViolation


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _workbook(manager, owner):
    workbook = manager.create_workbook(owner.id, "Style bounds")
    return int(workbook["id"]), int(workbook["sheets"][0]["id"])


def _seed_revision(manager, owner, wb_id, sheet_id):
    manager.apply_operation(
        owner.id,
        wb_id,
        sheet_id,
        "seed-style-bounds",
        "set_cell",
        0,
        [
            {
                "row": 0,
                "column": 0,
                "value_type": "number",
                "value": 1,
                "style": {"bold": True},
            }
        ],
    )
    return int(manager.get_workbook(owner.id, wb_id)["current_revision"])


def test_direct_style_bound_rejects_size_and_depth():
    oversized = {"color": "x" * (MAX_WORKSPACE_JSON_SERIALIZED_LENGTH + 1)}
    with pytest.raises(HardeningViolation):
        bound_workspace_json(oversized, field="cell style")

    nested = {}
    cursor = nested
    for _ in range(64):
        cursor["child"] = {}
        cursor = cursor["child"]
    with pytest.raises(HardeningViolation):
        bound_workspace_json(nested, field="cell style")

    # Non-object values are refused, None/ordinary dicts are accepted.
    with pytest.raises(HardeningViolation):
        bound_workspace_json(["not", "a", "dict"], field="cell style")
    assert bound_workspace_json(None) is None
    assert bound_workspace_json({"bold": True, "fill": {"color": "#fff"}}) == {
        "bold": True,
        "fill": {"color": "#fff"},
    }


def test_set_cell_rejects_oversized_style_without_mutating(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    revision = _seed_revision(manager, owner, wb_id, sheet_id)

    huge_style = {"color": "x" * (MAX_WORKSPACE_JSON_SERIALIZED_LENGTH + 1)}
    with pytest.raises(Exception) as exc:
        manager.apply_operation(
            owner.id,
            wb_id,
            sheet_id,
            "op-huge-style",
            "set_cell",
            revision,
            [
                {
                    "row": 1,
                    "column": 1,
                    "value_type": "text",
                    "value": "ok",
                    "style": huge_style,
                }
            ],
        )
    assert "style" in str(exc.value).lower()

    # Nothing from the rejected op was persisted: revision unchanged and the
    # replay log still only contains the seed, so an attacker cannot burn
    # revisions or bloat the durable log.
    assert int(manager.get_workbook(owner.id, wb_id)["current_revision"]) == revision
    replayed = manager.replay_operations(owner.id, wb_id)
    assert [op["operation_id"] for op in replayed] == ["seed-style-bounds"]


@pytest.mark.parametrize("operation_type", ["set_cell", "set_format", "clear_cell"])
def test_all_cell_operations_bound_style(db, two_users, server_manager, operation_type):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    revision = _seed_revision(manager, owner, wb_id, sheet_id)

    nested = {}
    cursor = nested
    for _ in range(64):
        cursor["child"] = {}
        cursor = cursor["child"]
    with pytest.raises(Exception):
        manager.apply_operation(
            owner.id,
            wb_id,
            sheet_id,
            f"op-deep-style-{operation_type}",
            operation_type,
            revision,
            [{"row": 2, "column": 2, "style": nested}],
        )
    assert int(manager.get_workbook(owner.id, wb_id)["current_revision"]) == revision


def test_legitimate_style_still_round_trips(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    style = {"bold": True, "fill": {"color": "#ffcccc"}, "fontSize": 12}
    manager.apply_operation(
        owner.id,
        wb_id,
        sheet_id,
        "op-normal-style",
        "set_cell",
        0,
        [
            {
                "row": 0,
                "column": 0,
                "value_type": "text",
                "value": "hi",
                "style": style,
            }
        ],
    )
    cell = manager.read_viewport(owner.id, wb_id, sheet_id, 0, 1, 0, 1)["cells"][0]
    assert cell["style"] == style


def test_validation_and_conditional_format_metadata_bounded(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    huge = {"value": "x" * (MAX_WORKSPACE_JSON_SERIALIZED_LENGTH + 1)}

    with pytest.raises(Exception):
        manager.set_data_validation(
            owner.id,
            wb_id,
            sheet_id,
            "list",
            0,
            10,
            0,
            10,
            huge,
            message="y" * (MAX_WORKSPACE_JSON_SERIALIZED_LENGTH + 1),
        )
    with pytest.raises(Exception):
        manager.set_conditional_format(
            owner.id, wb_id, sheet_id, "formula", 0, 10, 0, 10, huge, huge
        )

    # Small objects still persist normally.
    manager.set_conditional_format(
        owner.id,
        wb_id,
        sheet_id,
        "cell_value",
        0,
        10,
        0,
        10,
        {"operator": "gt", "value": 90},
        {"bold": True},
    )
    listed = manager.list_conditional_formats(owner.id, wb_id, sheet_id)
    assert len(listed) == 1
    assert listed[0]["style"] == {"bold": True}
