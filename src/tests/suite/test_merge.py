"""Server-side contract tests for merging local ``.plexisum`` edits.

The client merges offline edits by replaying a per-cell ``set_cell`` diff (a
locally-cleared cell is emitted as a blank set-cell). These tests pin that
contract: only the changed cells overwrite server state, untouched cells and
concurrent writes survive, and the idempotent operation-id replays cleanly.
"""

import pytest

from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum.manager import (
    SumConflictError,
    SumManager,
)


@pytest.fixture
def seeded_workbook(db, two_users, server_manager):
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Merge contract")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "seed-op",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 1},
            {"row": 0, "column": 1, "value_type": "text", "value": "keep"},
            {"row": 0, "column": 2, "value_type": "number", "value": 3},
        ],
    )
    return owner, manager, workbook, sheet_id


def test_local_merge_set_clear_and_preserve_untouched(seeded_workbook):
    owner, manager, workbook, sheet_id = seeded_workbook

    # The client's diff: A1 edited, B1 cleared (emitted as blank), C1 untouched,
    # plus a new formula at D1.
    result = manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "local-merge-1",
        "set_cell",
        1,
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 10},
            {"row": 0, "column": 1, "value_type": "blank", "value": None},
            {
                "row": 0,
                "column": 3,
                "value_type": "formula",
                "value": None,
                "formula": "A1*2",
            },
        ],
    )
    assert result == {"operation_id": "local-merge-1", "revision": 2, "replayed": False}

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 4
    )
    cells = {f"{cell['row']}:{cell['column']}": cell for cell in viewport["cells"]}
    assert cells["0:0"]["value"] == 10
    assert cells["0:1"]["value_type"] == "blank"
    assert cells["0:2"]["value"] == 3  # untouched cell survived the merge
    assert cells["0:3"]["value_type"] == "formula"
    assert cells["0:3"]["formula"] == "A1*2"


def test_local_merge_replay_is_idempotent(seeded_workbook):
    owner, manager, workbook, sheet_id = seeded_workbook
    cells = [{"row": 0, "column": 0, "value_type": "number", "value": 42}]

    first = manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "local-merge-2",
        "set_cell",
        1,
        cells,
    )
    replay = manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "local-merge-2",
        "set_cell",
        1,
        cells,
    )
    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["revision"] == first["revision"]

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 1
    )
    assert viewport["cells"][0]["value"] == 42


def test_local_merge_conflicts_on_stale_revision(seeded_workbook):
    owner, manager, workbook, sheet_id = seeded_workbook
    with pytest.raises(SumConflictError):
        # The workbook is at revision 1; a stale base_revision of 0 must fail
        # closed so a reconnect cannot silently clobber a concurrent write.
        manager.apply_operation(
            owner.id,
            int(workbook["id"]),
            int(sheet_id),
            "local-merge-3",
            "set_cell",
            0,
            [{"row": 0, "column": 0, "value_type": "number", "value": 99}],
        )
