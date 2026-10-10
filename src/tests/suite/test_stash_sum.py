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
    SumNotFoundError,
    SumPermissionError,
    SumStorageError,
    SumValidationError,
)
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState
from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.models import ArtifactType
from src.api.schemas.stash import StashFileUploadRequest
from pydantic import ValidationError


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


def test_stash_server_membership_pin_and_version(
    db, two_users, server_manager, media_manager
):
    owner, member = two_users
    server = server_manager.create_server(owner.id, "Suite server")
    server_manager.add_member(server.id, member.id)
    create_stash_tables(db)
    manager = StashManager(db, servers_module=server_manager, media=media_manager)

    # Stash references must point at real, active media objects; the manager
    # revalidates existence and ownership through the media boundary, so the
    # fixture uploads actual files rather than passing opaque strings.
    file_one = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"suite-file-one",
        filename="suite-file-1.txt",
        content_type="text/plain",
    )
    file_two = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"suite-file-two",
        filename="suite-file-2.txt",
        content_type="text/plain",
    )

    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Shared file",
        scope="server",
        server_id=server.id,
        media_object_id=str(file_one.file_id),
    )
    assert manager.list_items(member.id, server_id=server.id)[1] == 1

    pinned = manager.set_pinned(owner.id, item["id"], True)
    assert pinned["pinned"] is True
    version = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_two.file_id),
        content_type="text/plain",
        size_bytes=12,
        checksum="abc",
    )
    assert version["media_object_id"] == str(file_two.file_id)
    assert manager.get_item(owner.id, item["id"])["current_version_id"] == version["id"]
    assert manager.list_versions(member.id, item["id"])[0]["media_object_id"] is None
    assert manager.list_versions(member.id, item["id"])[0]["content_type"] is None


def test_stash_download_ticket_is_single_use_and_cleanup_is_safe(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    create_stash_tables(db)
    manager = StashManager(db, servers_module=server_manager, media=media_manager)
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"suite-ticket-file",
        filename="suite-ticket.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Opaque media reference",
        media_object_id=str(uploaded.file_id),
    )

    ticket = manager.create_download_ticket(owner.id, int(item["id"]), ttl_seconds=1)
    redeemed = manager.redeem_download_ticket(owner.id, ticket["ticket"])
    assert redeemed["media_object_id"] == str(uploaded.file_id)
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
    with pytest.raises(SumNotFoundError):
        manager.get_workbook(owner.id, int(workbook["id"]))
    with pytest.raises(SumPermissionError):
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

    with pytest.raises(SumPermissionError):
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
    """A CSV larger than one operation chunk imports one revision per chunk."""
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
    assert result["revision"] == 2

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 2, 0, 2
    )
    assert viewport["cells"][0]["value"] == "r0c0"
    # Replay returns both bounded operation chunks at strictly increasing
    # revisions (Agent 30 M4: every chunk used to share one result_revision,
    # making replay ordering nondeterministic).
    operations = manager.replay_operations(owner.id, int(workbook["id"]))
    assert len(operations) == 2
    assert [op["result_revision"] for op in operations] == [1, 2]
    assert [op["base_revision"] for op in operations] == [0, 1]


def test_sum_chunked_import_monotonic_revisions_and_segment_invalidation(
    db, two_users, server_manager
):
    """Agent 30 M4 regression: chunked bulk imports must assign a monotonic
    result revision per chunk and invalidate packed column segments over the
    imported ranges so stale segment values cannot resurface."""
    import json as json_module

    from src.core.suite.sum.segments import SegmentEncoder

    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Chunked import")
    sheet_id = int(workbook["sheets"][0]["id"])

    with manager._session_lock(int(workbook["id"])):
        connection = manager._materialized_connection_unlocked(int(workbook["id"]))
        cells = [
            {
                "row_index": row_index,
                "value_type": "text",
                "value_json": json_module.dumps("stale"),
                "formula": None,
                "style_json": None,
            }
            for row_index in range(4)
        ]
        for seg_id, seg_data in SegmentEncoder.pack_column(cells):
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
                    json_module.dumps(decoded.get("statistics", {})),
                ),
            )
        connection.commit()

    # Import overwrites all four segment-backed coordinates; 2500 cells force
    # the chunked path.
    content = "\n".join(",".join(f"v{r}c{c}" for c in range(50)) for r in range(50))
    result = manager.import_csv(owner.id, int(workbook["id"]), sheet_id, content, 0)
    assert result["cells"] == 2500

    operations = manager.replay_operations(owner.id, int(workbook["id"]))
    results = [op["result_revision"] for op in operations]
    assert len(results) >= 2
    assert results == sorted(set(results)), "chunk revisions must be unique + ordered"

    # Simulate the imported cells being cleared afterwards: if the chunked
    # import had left the packed segment intact, the gap-fill merge would
    # resurface its stale "overwritten" values through the cleared gaps.
    with manager._session_lock(int(workbook["id"])):
        connection = manager._materialized_connection_unlocked(int(workbook["id"]))
        connection.execute(
            "DELETE FROM sum_cells WHERE sheet_id = ? AND column_index = 0 AND row_index < 4",
            (str(sheet_id),),
        )
        connection.commit()

    viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), sheet_id, 0, 4, 0, 1
    )
    by_position = {(cell["row"], cell["column"]): cell for cell in viewport["cells"]}
    for row_index in range(4):
        assert (row_index, 0) not in by_position, (
            "cleared coordinate must not resurface a stale packed segment value"
        )


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


def test_sum_revision_blobs_are_pruned(
    db, two_users, server_manager, tmp_path, monkeypatch
):
    """Superseded immutable revision blobs are garbage-collected.

    Cell writes are normally coalesced (see test_sum_coalescing), so the
    prune path is exercised here by forcing a flush per operation.
    """
    import glob

    import src.core.suite.sum._shared as sum_shared_module

    monkeypatch.setattr(sum_shared_module, "SUM_FLUSH_MAX_OPS", 1)
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


def test_sum_structural_op_logs_row_with_primary_key(db, two_users, server_manager):
    """Regression: structural ops must log a non-NULL ``id`` in sum_operations.

    ``sum_operations.id`` is a ``BIGINT PRIMARY KEY`` with no default, so the
    durable main-database writer must supply it. The structural path used to
    omit the column; SQLite tolerates the resulting NULL, but PostgreSQL
    implies NOT NULL and rejected every structural edit. Asserting the logged
    row carries an id catches the omission on either backend.
    """
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "StructuralLog")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])
    manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        "seed",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": "a"}],
    )
    manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        "ins-log",
        "insert_rows",
        1,
        [{"row": 0, "column": 0, "count": 1}],
    )

    row = db.fetch_one(
        "SELECT id, result_revision FROM sum_operations "
        "WHERE workbook_id = ? AND operation_id = ?",
        (workbook_id, "ins-log"),
    )
    assert row is not None, "structural op must be durably logged"
    assert row["id"] is not None, (
        "sum_operations.id must be supplied explicitly; it is NOT NULL on PostgreSQL"
    )


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


def test_sum_snapshot_gc_keeps_metadata_when_blob_delete_is_unconfirmed(
    db, two_users, server_manager
):
    """GC retains a retry pointer when storage cannot confirm deletion."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    workbook = manager.create_workbook(owner.id, "Snapshot cleanup")
    for _ in range(50):
        manager.create_snapshot(owner.id, int(workbook["id"]))

    manager._delete_blob_confirmed = lambda _path: False
    manager.create_snapshot(owner.id, int(workbook["id"]))
    count = db.fetch_one(
        "SELECT COUNT(*) AS c FROM sum_snapshots WHERE workbook_id = ?",
        (int(workbook["id"]),),
    )["c"]
    assert count == 51


def test_sum_purge_retains_metadata_when_blob_delete_fails(
    db, two_users, server_manager, tmp_path
):
    """Account erasure fails closed instead of losing retryable blob paths."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Purge safety")
    workbook_id = int(workbook["id"])

    original_confirm = manager._delete_blob_confirmed
    manager._delete_blob_confirmed = lambda _path: False
    with pytest.raises(SumStorageError):
        manager.purge_workbook_by_id(workbook_id)
    assert (
        db.fetch_one("SELECT id FROM sum_workbooks WHERE id = ?", (workbook_id,))
        is not None
    )

    manager._delete_blob_confirmed = original_confirm
    manager.purge_workbook_by_id(workbook_id)
    assert (
        db.fetch_one("SELECT id FROM sum_workbooks WHERE id = ?", (workbook_id,))
        is None
    )
    assert (
        db.fetch_one(
            "SELECT id FROM artifacts WHERE id = ?", (int(workbook["artifact_id"]),)
        )
        is None
    )


def test_sum_artifact_delete_removes_linked_workbook_and_artifact(
    db, two_users, server_manager, tmp_path
):
    """Deleting a SUM artifact removes its workbook and artifact identity."""
    from src.core.suite import sum as sum_domain

    owner, _ = two_users
    manager = sum_domain.setup(
        db, servers_module=server_manager, storage_dir=str(tmp_path)
    )
    workbook = manager.create_workbook(owner.id, "Artifact lifecycle")
    workbook_id = int(workbook["id"])
    artifact_id = int(workbook["artifact_id"])
    assert ArtifactManager(db).delete(artifact_id, purge_media=False) is True
    assert (
        db.fetch_one("SELECT id FROM sum_workbooks WHERE id = ?", (workbook_id,))
        is None
    )
    assert db.fetch_one("SELECT id FROM artifacts WHERE id = ?", (artifact_id,)) is None
    assert not list((tmp_path / str(workbook_id)).rglob("*.plexisum"))


def test_sum_artifact_delete_rejects_missing_workbook_link(db, two_users):
    """Malformed SUM artifacts fail closed instead of deleting metadata."""
    owner, _ = two_users
    artifact = ArtifactManager(db).create(
        conversation_id=None,
        author_id=owner.id,
        artifact_type=ArtifactType.SUM,
        title="Malformed Sum",
        payload={},
        emit_event=False,
    )
    with pytest.raises(ValueError, match="workbook_id"):
        ArtifactManager(db).delete(artifact.id, purge_media=False)
    assert db.fetch_one("SELECT id FROM artifacts WHERE id = ?", (artifact.id,))


def test_sum_snapshot_insert_failure_cleans_blob(
    db, two_users, server_manager, monkeypatch, tmp_path
):
    """A failed snapshot metadata insert removes its external file."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Snapshot failure")
    original_execute = db.execute

    def fail_snapshot_insert(query, params=None, *args, **kwargs):
        if "INSERT INTO sum_snapshots" in query:
            raise RuntimeError("forced snapshot insert failure")
        return original_execute(query, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", fail_snapshot_insert)
    with pytest.raises(RuntimeError, match="forced snapshot insert failure"):
        manager.create_snapshot(owner.id, int(workbook["id"]))
    assert (
        db.fetch_one(
            "SELECT COUNT(*) AS c FROM sum_snapshots WHERE workbook_id = ?",
            (int(workbook["id"]),),
        )["c"]
        == 0
    )
    snapshot_files = list((tmp_path / str(workbook["id"])).glob("snapshot-*.plexisum"))
    assert snapshot_files == []


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


def test_sum_comment_mention_payload_uses_string_ids(
    db, two_users, server_manager, monkeypatch
):
    """WORKBOOK_UPDATE mention targets must be strings, not JSON numbers."""
    from src.core.events import gateway_emit
    from src.core.events.types import EventType

    owner, other = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Mentions")
    sheet_id = workbook["sheets"][0]["id"]

    captured = {}

    def fake_emit(event_type, data, **kwargs):
        captured["event_type"] = event_type
        captured["data"] = data

    monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
    manager.add_comment(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        0,
        0,
        f"Hey @{other.username} look here",
    )

    assert captured["event_type"] == EventType.WORKBOOK_UPDATE
    # A numeric id past 2^53 would lose precision in JS clients, so the
    # workspace mention toast could never match it against the viewer id.
    assert captured["data"]["mentions"] == [str(other.id)]
    assert captured["data"]["kind"] == "comment"


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


def test_sum_filter_contains_keeps_a_zero_target():
    """A 0 (or False) search term is not the empty string.

    ``str(target or "")`` turned a legitimate zero into "", so
    ``contains 0`` matched every non-empty cell and ``not_contains 0``
    matched none of them. Only a missing target is empty text.
    """
    from src.core.suite.sum.manager_viewport import SumManagerViewport

    matches = SumManagerViewport._matches_filter
    assert matches(105, "contains", 0) is True
    assert matches(15, "contains", 0) is False
    assert matches(15, "not_contains", 0) is True
    assert matches(105, "not_contains", 0) is False
    # A missing target still means "contains the empty string".
    assert matches("abc", "contains", None) is True
    # An ordering operator with no target has nothing to compare against.
    assert matches(1, "gte", None) is False
    # An operator read_viewport would have dropped is not a match.
    assert matches(1, "text", 1) is False


def test_sum_filter_reads_a_number_the_way_the_client_engine_does():
    """A numeric operand is compared as its number, not as Python prints it.

    The filter panel hands the value back as text for ``eq``/``neq``, and
    ``str(2.0)`` is "2.0", so "equals 2" missed every computed cell -- a
    formula result is a float -- while the client engine's ``String(2)`` is
    "2" and matched. Both sides now read the shared decimal rule and compare
    two numeric operands numerically, even when one arrived as text.
    """
    from src.core.suite.sum.manager_viewport import SumManagerViewport

    matches = SumManagerViewport._matches_filter
    assert matches(2.0, "eq", "2") is True
    assert matches(2.0, "neq", "2") is False
    assert matches(2.0, "contains", "2") is True
    assert matches(20.0, "contains", 0) is True
    # Text that reads as a number compares numerically on both sides.
    assert matches("2.50", "eq", 2.5) is True
    assert matches("2.50", "neq", 2.5) is False
    # A boolean is 1/0 numerically and "TRUE" as text.
    assert matches(True, "eq", 1) is True
    assert matches(True, "eq", "true") is True
    assert matches(True, "gt", 0) is True
    # Ordering needs a number on both sides. float() read "inf" as a number
    # (so it was greater than everything) and Number() reads "0x10" as one;
    # neither is a number in a filter.
    assert matches("inf", "gt", 1) is False
    assert matches("0x10", "gt", 1) is False
    assert matches("", "gt", -5) is False
    assert matches("abc", "gte", 1) is False
    assert matches(5, "gt", "abc") is False


def test_sum_xlsx_export_and_import(db, two_users, server_manager):
    pytest.importorskip("openpyxl")
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


def test_sum_xlsx_import_rejects_a_zip_bomb(db, two_users, server_manager):
    """A tiny XLSX that declares gigabytes must be rejected before parsing.

    ``openpyxl`` inflates the archive member by member, so without a
    declared-size check a few hundred kilobytes of upload can stall the
    worker on a multi-gigabyte worksheet before the row/column caps apply.
    The ZIP central directory states each member's uncompressed size, so the
    whole expansion is checked up front.
    """
    openpyxl = pytest.importorskip("openpyxl")
    import base64 as base64_module
    import io as io_module
    import zipfile

    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Bomb target")
    sheet_id = workbook["sheets"][0]["id"]

    # Start from a real workbook, then swap its worksheet for an oversized
    # one: the archive stays a valid XLSX shape, only the declared expansion
    # is implausible.
    good = io_module.BytesIO()
    openpyxl.Workbook().save(good)
    source = zipfile.ZipFile(io_module.BytesIO(good.getvalue()))
    namespace = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    oversized = (
        '<?xml version="1.0"?><worksheet '
        + namespace
        + '><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>'
        + ("x" * (80 * 1024 * 1024))
        + "</t></is></c></row></sheetData></worksheet>"
    )
    bomb = io_module.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in source.infolist():
            if item.filename == "xl/worksheets/sheet1.xml":
                archive.writestr(item.filename, oversized)
            else:
                archive.writestr(item, source.read(item.filename))
    content = base64_module.b64encode(bomb.getvalue()).decode("ascii")
    # The upload is well under the 20 MiB request bound, so only the
    # declared-expansion check can stop it.
    assert len(bomb.getvalue()) < 20 * 1024 * 1024

    with pytest.raises(SumValidationError, match="too large"):
        manager.import_xlsx(
            owner.id,
            int(workbook["id"]),
            int(sheet_id),
            content,
            int(workbook["current_revision"]),
        )


# ── PlexiSum home surface: pinned workbooks, templates, indexing status ──────


def test_sum_pin_round_trip_and_ordering(db, two_users, server_manager):
    """Pinning floats the workbook to the top of the owner's listing."""
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    first = manager.create_workbook(owner.id, "Alpha")
    # updated_at has millisecond resolution; space the creates so ordering is
    # deterministic instead of racing the same timestamp.
    time.sleep(0.005)
    manager.create_workbook(owner.id, "Beta")

    items, _ = manager.list_workbooks(owner.id)
    assert [w["title"] for w in items] == ["Beta", "Alpha"]
    assert all(not w["pinned"] for w in items)

    updated = manager.set_pinned(owner.id, int(first["id"]), True)
    assert updated["pinned"] is True
    assert updated["is_template"] is False

    items, _ = manager.list_workbooks(owner.id)
    assert [w["title"] for w in items] == ["Alpha", "Beta"]
    assert items[0]["pinned"] is True

    manager.set_pinned(owner.id, int(first["id"]), False)
    items, _ = manager.list_workbooks(owner.id)
    assert [w["title"] for w in items] == ["Beta", "Alpha"]
    assert all(not w["pinned"] for w in items)


def test_sum_pin_requires_edit_access(db, two_users, server_manager):
    """A view-only grantee cannot pin someone else's workbook."""
    owner, other = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Private")
    with pytest.raises(SumPermissionError):
        manager.set_pinned(other.id, int(workbook["id"]), True)


def test_sum_template_create_and_instantiate(db, two_users, server_manager):
    """A template can be seeded, listed separately, and copied into a new
    workbook without mutating the template or inheriting its template flag."""
    owner, other = two_users
    manager = _sum_manager(db, server_manager)
    template = manager.create_workbook(owner.id, "Budget template", template=True)
    assert template["is_template"] is True
    sheet_id = template["sheets"][0]["id"]
    _seed_cells(
        manager,
        owner,
        template,
        sheet_id,
        [(0, 0, 1), (1, 0, 2), (2, 0, 3)],
    )

    # Templates are excluded from the regular listing and appear in theirs.
    regular, _ = manager.list_workbooks(owner.id)
    assert [w["id"] for w in regular] == []
    templates, _ = manager.list_workbooks(owner.id, template=True)
    assert [w["id"] for w in templates] == [str(template["id"])]

    copy = manager.create_from_template(
        owner.id, int(template["id"]), "My budget", "personal", None
    )
    assert copy["is_template"] is False
    assert copy["title"] == "My budget"
    copy_sheet = copy["sheets"][0]["id"]
    viewport = manager.read_viewport(
        owner.id, int(copy["id"]), int(copy_sheet), 0, 50, 0, 20
    )
    values = {(c["row"], c["column"]): c["value"] for c in viewport["cells"]}
    assert values[(0, 0)] == 1
    assert values[(2, 0)] == 3

    # The template itself is untouched: still a template, same cells.
    template_again = manager.get_workbook(owner.id, int(template["id"]))
    assert template_again["is_template"] is True
    t_viewport = manager.read_viewport(
        owner.id, int(template["id"]), int(sheet_id), 0, 50, 0, 20
    )
    t_values = {(c["row"], c["column"]): c["value"] for c in t_viewport["cells"]}
    assert t_values[(0, 0)] == 1

    # Editing the copy must not affect the template.
    manager.apply_operation(
        owner.id,
        int(copy["id"]),
        int(copy_sheet),
        f"copy-write-{time.time_ns()}",
        "set_cell",
        int(copy["current_revision"]),
        [{"row": 0, "column": 0, "value_type": "number", "value": 99}],
    )
    t_viewport = manager.read_viewport(
        owner.id, int(template["id"]), int(sheet_id), 0, 50, 0, 20
    )
    t_values = {(c["row"], c["column"]): c["value"] for c in t_viewport["cells"]}
    assert t_values[(0, 0)] == 1


def test_sum_create_from_non_template_rejected(db, two_users, server_manager):
    """Only flagged templates can be instantiated."""
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Not a template")
    with pytest.raises(SumValidationError, match="not a template"):
        manager.create_from_template(owner.id, int(workbook["id"]), "Copy", "personal")


def test_sum_set_template_toggle(db, two_users, server_manager):
    """An existing workbook can be marked/unmarked as a template by its owner,
    moving it between the template and regular listings."""
    owner, other = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Budget")
    wb_id = int(workbook["id"])
    assert workbook["is_template"] is False

    marked = manager.set_template(owner.id, wb_id, True)
    assert marked["is_template"] is True
    regular, _ = manager.list_workbooks(owner.id)
    templates, _ = manager.list_workbooks(owner.id, template=True)
    assert [w["id"] for w in regular] == []
    assert [w["id"] for w in templates] == [str(wb_id)]

    unmarked = manager.set_template(owner.id, wb_id, False)
    assert unmarked["is_template"] is False
    regular, _ = manager.list_workbooks(owner.id)
    templates, _ = manager.list_workbooks(owner.id, template=True)
    assert [w["id"] for w in regular] == [str(wb_id)]
    assert [w["id"] for w in templates] == []


def test_sum_set_template_requires_edit_access(db, two_users, server_manager):
    """A non-editor cannot mark someone else's workbook as a template."""
    owner, other = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Private")
    with pytest.raises(SumPermissionError):
        manager.set_template(other.id, int(workbook["id"]), True)


def test_sum_workbook_status_reflects_cells(db, two_users, server_manager):
    """Materializing a workbook refreshes index/calculation state on the row."""
    owner, _ = two_users
    manager = _sum_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Status")
    assert workbook["index_state"] == "none"
    assert workbook["calculation_state"] == "none"
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        f"formula-{time.time_ns()}",
        "set_cell",
        int(workbook["current_revision"]),
        [
            {
                "row": 0,
                "column": 0,
                "value_type": "formula",
                "value": 2,
                "formula": "1+1",
            }
        ],
    )
    # Force a materialization (the read path opens the live session).
    manager.read_viewport(owner.id, int(workbook["id"]), int(sheet_id), 0, 50, 0, 20)
    refreshed = manager.get_workbook(owner.id, int(workbook["id"]))
    assert refreshed["calculation_state"] == "calculated"


def test_stash_file_upload_request_validates_payload():
    body = StashFileUploadRequest(
        title="Sheet",
        filename="sheet.plexisum",
        content="UFhTTQ==",  # "PXSM"
        content_type="application/x-plexisum",
        tags=["sheet", "sheet"],  # deduped by the manager, not the schema
    )
    assert body.title == "Sheet"
    assert body.filename == "sheet.plexisum"
    assert body.scope == "personal"

    with pytest.raises(ValidationError):
        StashFileUploadRequest(title="", filename="x", content="eA==")
    with pytest.raises(ValidationError):
        StashFileUploadRequest(title="x", filename="x", content="")
    with pytest.raises(ValidationError):
        StashFileUploadRequest(title="x", filename="x", content="eA==", scope="team")


def test_stash_upload_flow_creates_file_item(
    db, two_users, server_manager, media_manager
):
    """The endpoint-equivalent flow: bytes -> media object -> Stash file item."""
    owner, _ = two_users
    create_stash_tables(db)
    manager = StashManager(db, servers_module=server_manager, media=media_manager)
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"PXSM-container-bytes",
        filename="local.plexisum",
        content_type="application/x-plexisum",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Local workbook",
        media_object_id=str(uploaded.file_id),
        metadata={"size": uploaded.size, "content_type": uploaded.content_type},
        tags=["plexisum"],
    )
    fetched = manager.get_item(owner.id, item["id"])
    assert fetched["item_type"] == "file"
    assert fetched["metadata"]["content_type"] == "application/x-plexisum"
    assert "plexisum" in fetched["tags"]
