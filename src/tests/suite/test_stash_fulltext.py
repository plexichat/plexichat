"""Tests for Stash full-text search of file contents (ST1).

Text extracted from a stash item's media objects is stored in
``stash_item_text`` and matched by ``list_items(search=...)`` alongside title,
description, and tags.
"""

from src.core.suite.stash import StashManager
from src.core.suite.stash.schema import create_tables as create_stash_tables


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def _file_item(manager, media_manager, owner, title, content_bytes, name="doc.txt"):
    file_one = media_manager.upload_file(
        user_id=owner.id,
        file_data=content_bytes,
        filename=name,
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title=title,
        media_object_id=str(file_one.file_id),
    )
    return item


def test_search_matches_extracted_file_content(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    _file_item(
        manager,
        media_manager,
        owner,
        "Quarterly report",
        b"Revenue grew to 1.4 million in Q3.\nCosts remained flat.\n",
    )

    items, total = manager.list_items(owner.id, search="revenue")
    assert total == 1
    assert items[0]["title"] == "Quarterly report"

    # The same term also matches the title path.
    items, total = manager.list_items(owner.id, search="million")
    assert total == 1


def test_search_matches_content_from_version(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(manager, media_manager, owner, "Notes", b"nothing here\n")

    file_two = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"uniquetoken-zyxwv\n",
        filename="v2.txt",
        content_type="text/plain",
    )
    manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_two.file_id),
        content_type="text/plain",
        size_bytes=18,
        checksum="v2",
    )

    items, total = manager.list_items(owner.id, search="uniquetoken-zyxwv")
    assert total == 1


def test_search_does_not_match_binary_content(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    _file_item(
        manager,
        media_manager,
        owner,
        "Binary blob",
        b"\xff\xfe\x80\x81\x82",
    )

    # Binary bytes are not decoded, so search falls back to metadata only.
    items, total = manager.list_items(owner.id, search="blob")
    assert total == 1  # matches title
    items, total = manager.list_items(owner.id, search="\xff")
    assert total == 0
