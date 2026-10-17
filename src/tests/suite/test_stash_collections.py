"""Tests for nested PlexiStash collections and grant inheritance (ST2).

Collections may nest via ``parent_id``; a grant on a collection is inherited by
every item inside it (and its descendant collections), so a folder can be
shared once instead of per-item.
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import (
    StashPermissionError,
    StashValidationError,
)
from src.core.suite.stash.schema import create_tables as create_stash_tables


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def test_nested_collection_round_trip(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    parent = manager.create_collection(owner.id, "Parent")
    child = manager.create_collection(owner.id, "Child", parent_id=parent["id"])
    assert child["parent_id"] == parent["id"]

    listed = manager.list_collections(owner.id)
    by_id = {c["id"]: c for c in listed}
    assert by_id[child["id"]]["parent_id"] == parent["id"]
    assert by_id[parent["id"]]["parent_id"] is None


def test_nested_collection_requires_ownership(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)

    parent = manager.create_collection(owner.id, "Owned")
    with pytest.raises(StashPermissionError):
        manager.create_collection(other.id, "Nested", parent_id=parent["id"])


def test_nested_collection_scope_mismatch_rejected(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    server = server_manager.create_server(owner.id, "Suite server")

    personal = manager.create_collection(owner.id, "Personal")
    with pytest.raises(StashValidationError):
        manager.create_collection(
            owner.id, "Mismatched", server_id=server.id, parent_id=personal["id"]
        )


def test_collection_grant_inherited_by_items(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)

    collection = manager.create_collection(owner.id, "Shared folder")
    item = manager.create_item(
        owner.id,
        item_type="note",
        title="In folder",
        collection_id=int(collection["id"]),
    )
    with pytest.raises(StashPermissionError):
        manager.get_item(other.id, item["id"])

    manager.grant_collection(
        owner.id, int(collection["id"]), other.id, ["view_metadata"]
    )
    assert manager.get_item(other.id, item["id"])["id"] == item["id"]


def test_collection_grant_inherited_through_descendants(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)

    root = manager.create_collection(owner.id, "Root")
    child = manager.create_collection(owner.id, "Child", parent_id=root["id"])
    item = manager.create_item(
        owner.id,
        item_type="note",
        title="Deep item",
        collection_id=int(child["id"]),
    )

    manager.grant_collection(owner.id, int(root["id"]), other.id, ["view_metadata"])
    assert manager.get_item(other.id, item["id"])["id"] == item["id"]


def test_revoked_collection_grant_removes_item_access(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)

    collection = manager.create_collection(owner.id, "Folder")
    item = manager.create_item(
        owner.id,
        item_type="note",
        title="Note",
        collection_id=int(collection["id"]),
    )
    grant = manager.grant_collection(
        owner.id, int(collection["id"]), other.id, ["view_metadata"]
    )
    assert manager.get_item(other.id, item["id"])["id"] == item["id"]

    manager.revoke_collection_grant(owner.id, int(grant["id"]))
    with pytest.raises(StashPermissionError):
        manager.get_item(other.id, item["id"])


def test_collection_grant_restricted_to_delegable_permissions(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    collection = manager.create_collection(owner.id, "Folder")
    with pytest.raises(StashValidationError):
        manager.grant_collection(owner.id, int(collection["id"]), other.id, ["share"])
    with pytest.raises(StashValidationError):
        manager.grant_collection(owner.id, int(collection["id"]), other.id, ["delete"])


def test_non_owner_cannot_grant_collection(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    collection = manager.create_collection(owner.id, "Folder")
    with pytest.raises(StashPermissionError):
        manager.grant_collection(
            other.id, int(collection["id"]), other.id, ["view_metadata"]
        )
    with pytest.raises(StashPermissionError):
        manager.list_collection_grants(other.id, int(collection["id"]))


def test_list_items_filters_by_collection_id(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    folder_a = manager.create_collection(owner.id, "Folder A")
    folder_b = manager.create_collection(owner.id, "Folder B")

    a1 = manager.create_item(
        owner.id, item_type="note", title="A1", collection_id=int(folder_a["id"])
    )
    a2 = manager.create_item(
        owner.id, item_type="note", title="A2", collection_id=int(folder_a["id"])
    )
    b1 = manager.create_item(
        owner.id, item_type="note", title="B1", collection_id=int(folder_b["id"])
    )
    loose = manager.create_item(owner.id, item_type="note", title="Loose")

    a_items, a_total = manager.list_items(owner.id, collection_id=int(folder_a["id"]))
    a_ids = {i["id"] for i in a_items}
    assert a_total == 2
    assert {a1["id"], a2["id"]} <= a_ids
    assert b1["id"] not in a_ids
    assert loose["id"] not in a_ids

    b_items, b_total = manager.list_items(owner.id, collection_id=int(folder_b["id"]))
    assert b_total == 1
    assert b_items[0]["id"] == b1["id"]

    all_items, all_total = manager.list_items(owner.id)
    assert all_total == 4
