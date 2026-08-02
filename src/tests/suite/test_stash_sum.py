"""Focused PlexiSuite domain tests."""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.schema import create_tables as create_stash_tables
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.stash.manager import StashNotFoundError, StashPermissionError
from src.core.suite.sum.manager import SumConflictError, SumManager
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
