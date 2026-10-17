"""
PlexiStash backend coverage.

Exercises the core StashManager flows end-to-end against the shared test
database so the PlexiStash feature (metadata domain of PlexiSuite) is locked
in: owner-scoped items, versioning, collections, sharing (grants) and the
short-lived download-ticket flow used by the client thumbnail resolver.
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.schema import create_tables as create_stash_tables
from src.core.suite.stash.manager import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def test_stash_item_owner_scoping(db, two_users, server_manager, media_manager):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)

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


def test_stash_version_roundtrip(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    file_one = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"stash-version-1",
        filename="v1.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Versioned",
        metadata={"content_type": "text/plain"},
    )
    version = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_one.file_id),
        content_type="text/plain",
        size_bytes=len(b"stash-version-1"),
        checksum="deadbeef",
    )
    assert version["item_id"] == str(item["id"])
    versions = manager.list_versions(owner.id, item["id"])
    assert len(versions) == 1
    assert versions[0]["id"] == version["id"]


def test_stash_collection_crud(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    collection = manager.create_collection(owner.id, "My Collection")
    assert collection["name"] == "My Collection"

    listed = manager.list_collections(owner.id)
    assert any(c["id"] == collection["id"] for c in listed)

    with pytest.raises(StashValidationError):
        manager.create_collection(owner.id, "   ")


def test_stash_grant_gives_other_user_access(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)

    item = manager.create_item(
        owner.id, item_type="note", title="Shared note", metadata={"content": "x"}
    )
    with pytest.raises(StashPermissionError):
        manager.get_item(other.id, item["id"])

    manager.grant(owner.id, item["id"], other.id, ["view_metadata"])
    # The granted user can now read the item.
    assert manager.get_item(other.id, item["id"])["id"] == item["id"]


def test_stash_download_ticket_flow(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    file_one = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"stash-download",
        filename="dl.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Downloadable",
        metadata={"content_type": "text/plain"},
    )
    manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_one.file_id),
        content_type="text/plain",
        size_bytes=len(b"stash-download"),
        checksum="cafe",
    )

    ticket = manager.create_download_ticket(owner.id, item["id"])
    assert ticket["ticket"]

    redeemed = manager.redeem_download_ticket(owner.id, ticket["ticket"])
    assert redeemed["item_id"] == str(item["id"])

    # The same ticket cannot be redeemed twice.
    with pytest.raises(StashPermissionError):
        manager.redeem_download_ticket(owner.id, ticket["ticket"])


def test_stash_deleted_item_is_not_found(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    item = manager.create_item(owner.id, item_type="note", title="Ephemeral")
    manager.delete_item(owner.id, item["id"])
    with pytest.raises(StashNotFoundError):
        manager.get_item(owner.id, item["id"])
