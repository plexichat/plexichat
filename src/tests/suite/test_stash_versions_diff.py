"""Tests for Stash version preview/diff (ST4).

``diff_versions`` compares two stored versions (or a version vs. current) and
returns a structured line diff for text content and a size/checksum fallback
for binary content. Access requires ``download``.
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import (
    StashNotFoundError,
    StashPermissionError,
)
from src.core.suite.stash.schema import create_tables as create_stash_tables


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def _upload(media_manager, owner, data, name):
    return media_manager.upload_file(
        user_id=owner.id, file_data=data, filename=name, content_type="text/plain"
    )


def test_diff_two_text_versions(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    file_a = _upload(media_manager, owner, b"alpha\nbeta\ngamma\n", "a.txt")
    file_b = _upload(media_manager, owner, b"alpha\nBETA\ndelta\n", "b.txt")
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Diffable",
        media_object_id=str(file_a.file_id),
    )
    v1 = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_a.file_id),
        content_type="text/plain",
        size_bytes=16,
        checksum="a",
    )
    v2 = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_b.file_id),
        content_type="text/plain",
        size_bytes=16,
        checksum="b",
    )

    diff = manager.diff_versions(
        owner.id, int(item["id"]), int(v1["id"]), int(v2["id"])
    )
    assert diff["changed"] is True
    assert diff["binary"] is False
    # The unified op stream must contain at least one non-equal segment.
    ops = [op["op"] for op in diff["diff"]]
    assert any(op in ("insert", "delete", "replace") for op in ops)


def test_diff_identical_versions_is_empty(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    file_a = _upload(media_manager, owner, b"same\n", "a.txt")
    item = manager.create_item(
        owner.id, item_type="file", title="Same", media_object_id=str(file_a.file_id)
    )
    v1 = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_a.file_id),
        content_type="text/plain",
        size_bytes=5,
        checksum="a",
    )

    diff = manager.diff_versions(
        owner.id, int(item["id"]), int(v1["id"]), int(v1["id"])
    )
    assert diff["changed"] is False
    assert diff["binary"] is False
    assert all(op["op"] == "equal" for op in diff["diff"])


def test_diff_binary_content_uses_size_fallback(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)

    # Invalid UTF-8 bytes force the binary fallback even under a text/plain
    # content type (the decoder fails and the diff reports sizes only).
    file_a = _upload(media_manager, owner, b"\xff\xfe\x80\x81", "a.txt")
    file_b = _upload(media_manager, owner, b"\xff\xfe\x80\x82", "b.txt")
    item = manager.create_item(
        owner.id, item_type="file", title="Binary", media_object_id=str(file_a.file_id)
    )
    v1 = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_a.file_id),
        content_type="application/octet-stream",
        size_bytes=4,
        checksum="a",
    )
    v2 = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_b.file_id),
        content_type="application/octet-stream",
        size_bytes=4,
        checksum="b",
    )

    diff = manager.diff_versions(
        owner.id, int(item["id"]), int(v1["id"]), int(v2["id"])
    )
    assert diff["binary"] is True
    assert diff["changed"] is True
    assert diff["a_size"] == 4
    assert diff["b_size"] == 4


def test_diff_requires_download_access(db, two_users, server_manager, media_manager):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)

    file_a = _upload(media_manager, owner, b"secret\n", "a.txt")
    item = manager.create_item(
        owner.id, item_type="file", title="Secret", media_object_id=str(file_a.file_id)
    )
    v1 = manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_a.file_id),
        content_type="text/plain",
        size_bytes=7,
        checksum="a",
    )
    with pytest.raises(StashPermissionError):
        manager.diff_versions(other.id, int(item["id"]), int(v1["id"]))
    # A metadata-only grant must not unlock the diff.
    manager.grant(owner.id, item["id"], other.id, ["view_metadata"])
    with pytest.raises(StashPermissionError):
        manager.diff_versions(other.id, int(item["id"]), int(v1["id"]))


def test_diff_missing_version_is_not_found(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    file_a = _upload(media_manager, owner, b"x\n", "a.txt")
    item = manager.create_item(
        owner.id, item_type="file", title="Missing", media_object_id=str(file_a.file_id)
    )
    with pytest.raises(StashNotFoundError):
        manager.diff_versions(owner.id, int(item["id"]), 999_999)
