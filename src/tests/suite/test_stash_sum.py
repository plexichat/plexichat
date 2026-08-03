"""Focused PlexiSuite domain tests."""

import time

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.schema import create_tables as create_stash_tables
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.stash.manager import StashNotFoundError, StashPermissionError
from src.core.suite.sum.manager import (
    SumConflictError,
    SumManager,
    SumValidationError,
)
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState


def test_stash_personal_items_are_owner_scoped(db, two_users, server_manager):
    owner, other = two_users
    create_stash_tables(db)
    manager = StashManager(db, servers_module=server_manager)

    item = manager.create_item(
        owner.id,
        item_type="note",
        title="Private note",
        metadata={"content": "secret"},
        tags=["private"],
    )

    assert manager.get_item(owner.id, item["id"])["title"] == "Private note"
    with pytest.raises(StashPermissionError):
        manager.get_item(other.id, item["id"])


def test_stash_server_membership_pin_and_version(db, two_users, server_manager):
    owner, member = two_users
    server = server_manager.create_server(owner.id, "Suite server")
    server_manager.add_member(server.id, member.id)
    create_stash_tables(db)
    manager = StashManager(db, servers_module=server_manager)

    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Shared file",
        scope="server",
        server_id=server.id,
        media_object_id="media-1",
    )
    assert manager.list_items(member.id, server_id=server.id)[1] == 1

    pinned = manager.set_pinned(owner.id, item["id"], True)
    assert pinned["pinned"] is True
    version = manager.create_version(
        owner.id,
        item["id"],
        media_object_id="media-2",
        content_type="text/plain",
        size_bytes=12,
        checksum="abc",
    )
    assert version["media_object_id"] == "media-2"
    assert manager.get_item(owner.id, item["id"])["current_version_id"] == version["id"]
    assert manager.list_versions(member.id, item["id"])[0]["media_object_id"] is None
    assert manager.list_versions(member.id, item["id"])[0]["content_type"] is None


def test_stash_download_ticket_is_single_use_and_cleanup_is_safe(
    db, two_users, server_manager
):
    owner, _ = two_users
    create_stash_tables(db)
    manager = StashManager(db, servers_module=server_manager)
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Opaque media reference",
        media_object_id="media-file-name-or-key",
    )

    ticket = manager.create_download_ticket(owner.id, int(item["id"]), ttl_seconds=1)
    redeemed = manager.redeem_download_ticket(owner.id, ticket["ticket"])
    assert redeemed["media_object_id"] == "media-file-name-or-key"
    with pytest.raises(StashPermissionError):
        manager.redeem_download_ticket(owner.id, ticket["ticket"])

    expired = manager.create_download_ticket(owner.id, int(item["id"]), ttl_seconds=1)
    db.execute(
        "UPDATE stash_download_tickets SET expires_at = ? WHERE token_hash = ?",
        (0, __import__("hashlib").sha256(expired["ticket"].encode()).hexdigest()),
    )
    # The redeemed ticket and the explicitly expired ticket are both eligible
    # for cleanup; audit rows remain intact.
    assert manager.cleanup_expired_tickets() == 2


def test_sum_audit_records_workbook_and_operation(db, two_users, server_manager):
    owner, _ = two_users
    create_stash_tables(db)
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Audited metrics")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "audit-op-1",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "number", "value": 1}],
    )
    rows = db.fetch_all(
        "SELECT action, resource_type FROM suite_audit_log "
        "WHERE domain = 'sum' AND actor_id = ? ORDER BY created_at, id",
        (owner.id,),
    )
    assert [(row["action"], row["resource_type"]) for row in rows] == [
        ("create", "workbook"),
        ("edit", "operation"),
    ]


def test_sum_route_fails_closed_when_capability_is_unavailable(monkeypatch):
    from fastapi import HTTPException
    from src.api.routes.suite import sum as sum_routes

    monkeypatch.setattr(
        sum_routes,
        "get_capability",
        lambda _feature: CapabilityInfo(
            "plexisum", CapabilityState.DISABLED_BY_LICENSE, "disabled"
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        sum_routes._require_sum_capability()
    assert exc_info.value.status_code == 403


def test_stash_delete_restore_is_owner_only(db, two_users, server_manager):
    owner, other = two_users
    create_stash_tables(db)
    manager = StashManager(db, servers_module=server_manager)
    item = manager.create_item(owner.id, item_type="note", title="Recoverable")
    manager.delete_item(owner.id, int(item["id"]))

    with pytest.raises(StashPermissionError):
        manager.restore_item(other.id, int(item["id"]))
    # Deleted resources intentionally collapse to 404 for unauthorized users
    # so the API cannot be used to enumerate prior resource IDs.
    with pytest.raises(StashNotFoundError):
        manager.get_item(other.id, int(item["id"]))

    restored = manager.restore_item(owner.id, int(item["id"]))
    assert restored["title"] == "Recoverable"
    actions = db.fetch_all(
        "SELECT action FROM suite_audit_log WHERE domain = 'stash' "
        "AND resource_id = ? ORDER BY created_at, id",
        (int(item["id"]),),
    )
    assert [row["action"] for row in actions] == ["create", "delete", "restore"]


def test_sum_delete_restore_preserves_sheet_data(db, two_users, server_manager):
    owner, other = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Recoverable workbook")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "restore-op",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": "kept"}],
    )
    manager.delete_workbook(owner.id, int(workbook["id"]))
    with pytest.raises(Exception):
        manager.get_workbook(owner.id, int(workbook["id"]))
    with pytest.raises(Exception):
        manager.restore_workbook(other.id, int(workbook["id"]))
    restored = manager.restore_workbook(owner.id, int(workbook["id"]))
    assert restored["current_revision"] == 1
    assert (
        manager.read_viewport(owner.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 1)[
            "cells"
        ][0]["value"]
        == "kept"
    )


def test_sum_sparse_viewport_revision_conflict_and_replay(
    db, two_users, server_manager
):
    owner, other = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Metrics")
    sheet_id = workbook["sheets"][0]["id"]

    result = manager.apply_operation(
        owner.id,
        workbook["id"],
        sheet_id,
        "op-1",
        "set_cell",
        0,
        [{"row": 2, "column": 3, "value_type": "number", "value": 42}],
    )
    assert result == {"operation_id": "op-1", "revision": 1, "replayed": False}

    replay = manager.apply_operation(
        owner.id,
        workbook["id"],
        sheet_id,
        "op-1",
        "set_cell",
        0,
        [{"row": 2, "column": 3, "value_type": "number", "value": 42}],
    )
    assert replay["replayed"] is True

    viewport = manager.read_viewport(owner.id, workbook["id"], sheet_id, 0, 5, 0, 5)
    assert viewport["revision"] == 1
    assert viewport["cells"][0]["value"] == 42

    with pytest.raises(SumConflictError):
        manager.apply_operation(
            owner.id,
            workbook["id"],
            sheet_id,
            "op-2",
            "set_cell",
            0,
            [{"row": 0, "column": 0, "value_type": "text", "value": "stale"}],
        )

    with pytest.raises(Exception):
        manager.get_workbook(other.id, workbook["id"])


def test_sum_list_includes_grant_shared_workbooks(db, two_users, server_manager):
    """Workbooks shared via a live grant appear in the recipient's list."""
    owner, other = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Shared workbook")

    listed, total = manager.list_workbooks(other.id)
    assert total == 0

    grant = manager.grant_workbook(owner.id, int(workbook["id"]), other.id, ["view"])
    listed, total = manager.list_workbooks(other.id)
    assert total == 1
    assert listed[0]["id"] == str(workbook["id"])

    manager.revoke_grant(owner.id, int(grant["id"]))
    listed, total = manager.list_workbooks(other.id)
    assert total == 0


def test_sum_csv_import_exceeds_single_operation_limit(db, two_users, server_manager):
    """A CSV larger than one operation chunk imports in a single revision."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Big import")
    sheet_id = workbook["sheets"][0]["id"]

    # 30 rows x 100 columns = 3000 non-empty cells (> MAX_OPERATION_CELLS=2000).
    content = "\n".join(",".join(f"r{r}c{c}" for c in range(100)) for r in range(30))
    result = manager.import_csv(
        owner.id, int(workbook["id"]), int(sheet_id), content, 0
    )
    assert result["cells"] == 3000
    assert result["revision"] == 1

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 2, 0, 2
    )
    assert viewport["cells"][0]["value"] == "r0c0"
    # Replay returns both bounded operation chunks.
    operations = manager.replay_operations(owner.id, int(workbook["id"]))
    assert len(operations) == 2


def test_sum_download_file_is_base64_and_round_trips(db, two_users, server_manager):
    """Download returns base64 binary-safe content; upload accepts it back."""
    import base64 as base64_module

    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Download")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "op-x",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": "héllo"}],
    )

    result = manager.download_file(owner.id, int(workbook["id"]))
    decoded = base64_module.b64decode(result["content"])
    assert decoded[:15] == b"SQLite format 3"
    assert result["size"] == len(decoded)

    upload = manager.upload_file(owner.id, int(workbook["id"]), decoded)
    assert upload["revision"] == 2


def test_sum_revision_blobs_are_pruned(db, two_users, server_manager, tmp_path):
    """Superseded immutable revision blobs are garbage-collected."""
    import glob

    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Pruned")
    sheet_id = workbook["sheets"][0]["id"]
    for i in range(12):
        manager.apply_operation(
            owner.id,
            int(workbook["id"]),
            int(sheet_id),
            f"op-{i}",
            "set_cell",
            i,
            [{"row": i, "column": 0, "value_type": "number", "value": i}],
        )

    files = sorted(glob.glob(str(tmp_path / str(workbook["id"]) / "rev-*.plexisum")))
    # After 12 operations the current revision is 12; the cache keeps only
    # the newest REVISION_KEEP_COUNT + current blob (rev-7..rev-12).
    assert len(files) == 6


def test_sum_snapshot_reports_real_cell_count(db, two_users, server_manager):
    """create_snapshot returns the actual cell count, not a hardcoded zero."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Snapshot count")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "op-s",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 1},
            {"row": 1, "column": 1, "value_type": "text", "value": "x"},
        ],
    )
    snap = manager.create_snapshot(owner.id, int(workbook["id"]))
    assert snap["cell_count"] == 2


def test_sum_index_promotion_waits_for_data(db, two_users, server_manager):
    """A column that crosses the threshold while empty is promoted once data lands."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Index wait")
    sheet_id = workbook["sheets"][0]["id"]

    # 50 blank writes push column 0 over the promotion threshold without any
    # indexable entries, leaving a pending marker behind.
    for i in range(50):
        manager.apply_operation(
            owner.id,
            int(workbook["id"]),
            int(sheet_id),
            f"idx-wait-{i}",
            "set_cell",
            i,
            [{"row": i, "column": 0, "value_type": "blank", "value": None}],
        )

    # The next op that writes a real value to the column should promote it.
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "idx-wait-data",
        "set_cell",
        50,
        [{"row": 50, "column": 0, "value_type": "number", "value": 1}],
    )

    data = manager._file_bytes(int(workbook["id"]))
    connection, path = manager._open_sqlite(data)
    try:
        states = [
            row["state"]
            for row in connection.execute(
                "SELECT state FROM sum_indexes WHERE column_key = '0'"
            ).fetchall()
        ]
    finally:
        manager._close_sqlite(connection, path)
    assert "ready" in states


def test_sum_viewport_returns_computed_formula_values(db, two_users, server_manager):
    """Formula cells in a viewport carry a live ``computed`` value."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Formulas")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "f-1",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "number", "value": 10}],
    )
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "f-2",
        "set_cell",
        1,
        [
            {
                "row": 1,
                "column": 0,
                "value_type": "formula",
                "value": None,
                "formula": "A1*2",
            },
            {
                "row": 1,
                "column": 1,
                "value_type": "formula",
                "value": None,
                "formula": "SUM(A1,B1)",
            },
        ],
    )
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 2, 0, 2
    )
    by_position = {(c["row"], c["column"]): c for c in viewport["cells"]}
    assert by_position[(1, 0)]["computed"] == 20.0
    assert by_position[(1, 1)]["computed"] == 10.0


def test_sum_viewport_reports_dimensions_and_has_more(db, two_users, server_manager):
    """row_count/column_count reflect the sheet extent; has_more flags overflow."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Extents")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "ext-1",
        "set_cell",
        0,
        [{"row": 4, "column": 2, "value_type": "number", "value": 1}],
    )
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 2, 0, 2
    )
    assert viewport["row_count"] == 5
    assert viewport["column_count"] == 3
    assert viewport["has_more"] is True
    full = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 5, 0, 3
    )
    assert full["has_more"] is False


def test_sum_structural_row_ops_shift_cells(db, two_users, server_manager):
    """Inserting/deleting rows shifts coordinates and axis metadata together."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Rows")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "r-1",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "text", "value": "top"},
            {"row": 2, "column": 0, "value_type": "text", "value": "bottom"},
        ],
    )

    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "r-ins",
        "insert_rows",
        1,
        [{"row": 1, "column": 0, "count": 2}],
    )
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 5, 0, 1
    )
    cells = {(c["row"], c["column"]): c["value"] for c in viewport["cells"]}
    assert cells[(0, 0)] == "top"
    assert cells[(4, 0)] == "bottom"

    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "r-del",
        "delete_rows",
        2,
        [{"row": 0, "column": 0, "count": 2}],
    )
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 4, 0, 1
    )
    cells = {(c["row"], c["column"]): c["value"] for c in viewport["cells"]}
    # Deleting rows 0-1 removes 'top' and shifts 'bottom' from row 4 to row 2.
    assert (0, 0) not in cells
    assert cells[(2, 0)] == "bottom"


def test_sum_structural_column_ops_shift_cells(db, two_users, server_manager):
    """Inserting/deleting columns shifts cell coordinates."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Columns")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "c-1",
        "set_cell",
        0,
        [{"row": 0, "column": 2, "value_type": "text", "value": "far"}],
    )
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "c-ins",
        "insert_columns",
        1,
        [{"row": 0, "column": 1, "count": 1}],
    )
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 4
    )
    cells = {(c["row"], c["column"]): c["value"] for c in viewport["cells"]}
    assert cells[(0, 3)] == "far"

    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "c-del",
        "delete_columns",
        2,
        [{"row": 0, "column": 2, "count": 1}],
    )
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 4
    )
    cells = {(c["row"], c["column"]): c["value"] for c in viewport["cells"]}
    assert cells[(0, 2)] == "far"


def test_sum_sort_range_orders_rows(db, two_users, server_manager):
    """Server-side sort stably orders rows by a column's computed value."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Sorted")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "s-1",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 30},
            {"row": 1, "column": 0, "value_type": "number", "value": 10},
            {"row": 2, "column": 0, "value_type": "number", "value": 20},
        ],
    )
    result = manager.sort_range(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        column=0,
        start_row=0,
        end_row=3,
        direction="asc",
        base_revision=1,
    )
    assert result["rows"] == 3
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 3, 0, 1
    )
    values = [c["value"] for c in sorted(viewport["cells"], key=lambda c: c["row"])]
    assert values == [10, 20, 30]


def test_sum_sort_desc_keeps_blanks_last(db, two_users, server_manager):
    """Descending sort orders values in-group and keeps blanks last."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Desc sort")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "d-1",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 10},
            {"row": 1, "column": 0, "value_type": "number", "value": 30},
            {"row": 2, "column": 0, "value_type": "text", "value": "apple"},
        ],
    )
    manager.sort_range(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        column=0,
        start_row=0,
        end_row=4,
        direction="desc",
        base_revision=1,
    )
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 4, 0, 1
    )
    values = [c["value"] for c in sorted(viewport["cells"], key=lambda c: c["row"])]
    # Numbers descend first, then text; blank rows stay at the bottom.
    assert values == [30, 10, "apple"]


def test_sum_csv_import_infers_types(db, two_users, server_manager):
    """CSV values land as typed cells (boolean / number / text)."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Typed")
    sheet_id = workbook["sheets"][0]["id"]
    content = "TRUE,42,hello\nFALSE,,3.5"
    manager.import_csv(owner.id, int(workbook["id"]), int(sheet_id), content, 0)
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 2, 0, 3
    )
    by_position = {(c["row"], c["column"]): c for c in viewport["cells"]}
    assert by_position[(0, 0)]["value_type"] == "boolean"
    assert by_position[(0, 0)]["value"] is True
    assert by_position[(0, 1)]["value_type"] == "number"
    assert by_position[(0, 2)]["value_type"] == "text"
    assert by_position[(1, 2)]["value_type"] == "number"
    assert by_position[(1, 2)]["value"] == 3.5


def test_sum_export_csv_contains_computed_values(db, two_users, server_manager):
    """CSV export renders formula cells as their computed values."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Export")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "e-1",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 7},
            {
                "row": 0,
                "column": 1,
                "value_type": "formula",
                "value": None,
                "formula": "A1*3",
            },
        ],
    )
    export = manager.export_csv(owner.id, int(workbook["id"]), int(sheet_id))
    assert export["content"].splitlines()[0].split(",") == ["7", "21"]


def test_sum_snapshot_retention_gc(db, two_users, server_manager):
    """Only the newest MAX_SNAPSHOTS_KEPT snapshots survive creation."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Snapshots")
    for _ in range(60):
        manager.create_snapshot(owner.id, int(workbook["id"]))
    count = db.fetch_one(
        "SELECT COUNT(*) AS c FROM sum_snapshots WHERE workbook_id = ?",
        (int(workbook["id"]),),
    )["c"]
    assert count == 50


def test_sum_axis_metadata_tracks_written_cells(db, two_users, server_manager):
    """Writing a cell records its row and column in the axis metadata tables."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Axis")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "ax-1",
        "set_cell",
        0,
        [{"row": 3, "column": 2, "value_type": "number", "value": 9}],
    )
    data = manager._file_bytes(int(workbook["id"]))
    connection, path = manager._open_sqlite(data)
    try:
        rows = connection.execute(
            "SELECT position FROM sum_rows WHERE sheet_id = ? AND position = 3",
            (str(sheet_id),),
        ).fetchone()
        columns = connection.execute(
            "SELECT display_name FROM sum_columns WHERE sheet_id = ? AND position = 2",
            (str(sheet_id),),
        ).fetchone()
    finally:
        manager._close_sqlite(connection, path)
    assert rows is not None
    assert columns is not None
    assert columns["display_name"] == "C"


# ── PlexiSum feature batch: freeze, validation, formats, comments, presence ──


def _sum_manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _seed_cells(manager, owner, workbook, sheet_id, values):
    """Write a grid of number cells: [(row, col, value), ...]."""
    cells = [
        {"row": r, "column": c, "value_type": "number", "value": v}
        for r, c, v in values
    ]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        f"seed-{time.time_ns()}",
        "set_cell",
        int(workbook["current_revision"]),
        cells,
    )


def test_sum_freeze_round_trip(db, two_users, server_manager):
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Frozen")
    sheet_id = workbook["sheets"][0]["id"]
    freeze = manager.set_freeze(owner.id, int(workbook["id"]), int(sheet_id), 2, 1)
    assert freeze["freeze_rows"] == 2
    assert freeze["freeze_columns"] == 1
    again = manager.get_freeze(owner.id, int(workbook["id"]), int(sheet_id))
    assert again["freeze_rows"] == 2
    assert again["freeze_columns"] == 1
    # Viewport metadata carries the freeze state.
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 50, 0, 20
    )
    assert viewport["freeze"]["freeze_rows"] == 2
    assert viewport["freeze"]["freeze_columns"] == 1


def test_sum_validation_enforced_on_write(db, two_users, server_manager):
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Validated")
    sheet_id = workbook["sheets"][0]["id"]

    def current_revision():
        return manager.get_workbook(owner.id, int(workbook["id"]))["current_revision"]

    manager.set_validation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "whole",
        0,
        10,
        0,
        5,
        {"min": 1, "max": 100},
        allow_blank=True,
        message="Must be 1-100",
    )
    # A value inside the rule range lands fine.
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        f"ok-{time.time_ns()}",
        "set_cell",
        current_revision(),
        [{"row": 1, "column": 1, "value_type": "number", "value": 50}],
    )
    # A value outside the range is rejected by the write path.
    with pytest.raises(SumValidationError) as exc_info:
        manager.apply_operation(
            owner.id,
            int(workbook["id"]),
            int(sheet_id),
            f"bad-{time.time_ns()}",
            "set_cell",
            current_revision(),
            [{"row": 2, "column": 1, "value_type": "number", "value": 500}],
        )
    assert "1-100" in str(exc_info.value)
    # Clearing a cell inside a rule range is always allowed.
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        f"clear-{time.time_ns()}",
        "clear_cell",
        current_revision(),
        [{"row": 1, "column": 1}],
    )
    # list rule with an allowed value
    manager.set_validation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "list",
        0,
        10,
        5,
        10,
        {"allowed": ["red", "green", "blue"]},
    )
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        f"list-ok-{time.time_ns()}",
        "set_cell",
        current_revision(),
        [{"row": 1, "column": 6, "value_type": "text", "value": "green"}],
    )
    with pytest.raises(SumValidationError):
        manager.apply_operation(
            owner.id,
            int(workbook["id"]),
            int(sheet_id),
            f"list-bad-{time.time_ns()}",
            "set_cell",
            current_revision(),
            [{"row": 2, "column": 6, "value_type": "text", "value": "purple"}],
        )


def test_sum_validation_delete_and_viewport(db, two_users, server_manager):
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Validation meta")
    sheet_id = workbook["sheets"][0]["id"]
    created = manager.set_validation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "length",
        0,
        5,
        0,
        3,
        {"min": 2, "max": 10},
        message="2-10 chars",
    )
    listed = manager.list_validations(owner.id, int(workbook["id"]), int(sheet_id))
    assert len(listed) == 1
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 50, 0, 20
    )
    assert len(viewport["validations"]) == 1
    assert viewport["validations"][0]["message"] == "2-10 chars"
    manager.delete_validation(owner.id, int(workbook["id"]), int(created["id"]))
    assert manager.list_validations(owner.id, int(workbook["id"]), int(sheet_id)) == []


def test_sum_conditional_format_round_trip(db, two_users, server_manager):
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Conditional")
    sheet_id = workbook["sheets"][0]["id"]
    rule = manager.set_conditional_format(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "cell_value",
        0,
        100,
        0,
        10,
        {"operator": "gt", "value": 90},
        {"bold": True, "fill": {"color": "#ffcccc"}},
    )
    listed = manager.list_conditional_formats(
        owner.id, int(workbook["id"]), int(sheet_id)
    )
    assert len(listed) == 1
    assert listed[0]["style"]["bold"] is True
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 50, 0, 20
    )
    assert len(viewport["conditional_formats"]) == 1
    manager.delete_conditional_format(owner.id, int(workbook["id"]), str(rule["id"]))
    assert (
        manager.list_conditional_formats(owner.id, int(workbook["id"]), int(sheet_id))
        == []
    )


def test_sum_comments_crud_and_viewport_markers(db, two_users, server_manager):
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Comments")
    sheet_id = workbook["sheets"][0]["id"]
    comment = manager.add_comment(
        owner.id, int(workbook["id"]), int(sheet_id), 3, 2, "Check this cell"
    )
    assert comment["body"] == "Check this cell"
    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 50, 0, 20
    )
    assert len(viewport["comments"]) == 1
    assert viewport["comments"][0]["row"] == 3
    assert viewport["comments"][0]["column"] == 2
    manager.resolve_comment(owner.id, int(workbook["id"]), str(comment["id"]), True)
    listed = manager.list_comments(owner.id, int(workbook["id"]), int(sheet_id), 3, 2)
    assert listed[0]["resolved"] is True
    manager.delete_comment(owner.id, int(workbook["id"]), str(comment["id"]))
    assert manager.list_comments(owner.id, int(workbook["id"]), int(sheet_id)) == []


def test_sum_presence_heartbeat_and_prune(db, two_users, server_manager):
    owner, other = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Presence")
    manager.grant_workbook(owner.id, int(workbook["id"]), other.id, ["edit"])
    users = manager.set_presence(owner.id, int(workbook["id"]), username="owner")
    assert any(u["user_id"] == owner.id for u in users)
    manager.set_presence(other.id, int(workbook["id"]), username="other")
    listed = manager.list_presence(owner.id, int(workbook["id"]))
    assert len(listed) == 2
    # Stale entries older than the TTL are pruned on read.
    import time as time_module

    from src.core.suite.sum.manager import _WORKBOOK_PRESENCE, _prune_presence

    workbook_key = int(workbook["id"])
    for uid in list(_WORKBOOK_PRESENCE.get(workbook_key, {})):
        _WORKBOOK_PRESENCE[workbook_key][uid]["ts"] = time_module.time() - 9999
    _prune_presence()
    assert _WORKBOOK_PRESENCE.get(workbook_key, {}) == {}


def test_sum_viewport_filter_hides_rows(db, two_users, server_manager):
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Filtered")
    sheet_id = workbook["sheets"][0]["id"]
    _seed_cells(
        manager,
        owner,
        workbook,
        sheet_id,
        [(0, 0, 10), (1, 0, 20), (2, 0, 30), (0, 1, 100), (1, 1, 200)],
    )
    viewport = manager.read_viewport(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        0,
        50,
        0,
        20,
        filters=[{"column": 0, "operator": "gte", "value": 20}],
    )
    assert viewport["hidden_rows"] == [0]
    assert viewport["filters"][0]["operator"] == "gte"
    # A contains filter on text
    viewport2 = manager.read_viewport(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        0,
        50,
        0,
        20,
        filters=[{"column": 1, "operator": "is_not_empty", "value": None}],
    )
    assert viewport2["hidden_rows"] == [2]


def test_sum_xlsx_export_and_import(db, two_users, server_manager):
    openpyxl = pytest.importorskip("openpyxl")
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "XLSX bridge")
    sheet_id = workbook["sheets"][0]["id"]
    _seed_cells(
        manager,
        owner,
        workbook,
        sheet_id,
        [(0, 0, 1), (1, 0, 2), (2, 0, 3)],
    )
    exported = manager.export_xlsx(owner.id, int(workbook["id"]), int(sheet_id))
    import base64 as base64_module

    raw = base64_module.b64decode(exported["content"])
    assert raw.startswith(b"PK")
    # Round-trip: import into a fresh workbook and verify values land.
    second = manager.create_workbook(owner.id, "Imported")
    sheet2_id = second["sheets"][0]["id"]
    result = manager.import_xlsx(
        owner.id,
        int(second["id"]),
        int(sheet2_id),
        exported["content"],
        int(second["current_revision"]),
    )
    assert result["cells"] >= 3
    viewport = manager.read_viewport(
        owner.id, int(second["id"]), int(sheet2_id), 0, 50, 0, 20
    )
    values = {(c["row"], c["column"]): c["value"] for c in viewport["cells"]}
    assert values[(0, 0)] == 1
    assert values[(2, 0)] == 3
