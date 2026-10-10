"""Tests for PlexiStash share links (opaque, optionally password-protected).

Share links grant read-only access to any authenticated user in possession of
the token (and password, when set). The token is returned exactly once and only
its SHA-256 digest is persisted. These tests lock in the security boundary:
fail-closed errors, no token leakage, no privileged grants, and revocation.
"""

import time
from unittest.mock import patch

import pytest

import src.utils.encryption as encryption
from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)
from src.core.suite.stash.schema import create_tables as create_stash_tables


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def _file_item(owner, manager, media_manager, title="Shareable"):
    file_one = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"stash-share-link-bytes",
        filename="share.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title=title,
        metadata={"content_type": "text/plain"},
    )
    manager.create_version(
        owner.id,
        item["id"],
        media_object_id=str(file_one.file_id),
        content_type="text/plain",
        size_bytes=len(b"stash-share-link-bytes"),
        checksum="beef",
    )
    return item


def test_share_link_redeem_by_other_user(db, two_users, server_manager, media_manager):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"])
    assert link["token"]
    assert link["password_protected"] is False

    resolved = manager.resolve_share_link(other.id, link["token"])
    assert resolved["item_id"] == str(item["id"])
    assert resolved["media_object_id"]
    assert "download" in resolved["permissions"]


def test_password_protected_share_link_requires_password(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"], password="hunter2")
    assert link["password_protected"] is True

    # Missing password is rejected before verification even runs.
    with pytest.raises(StashPermissionError):
        manager.resolve_share_link(other.id, link["token"])
    # Wrong password is rejected when verification fails.
    with patch.object(encryption, "verify_password", return_value=False):
        with pytest.raises(StashPermissionError):
            manager.resolve_share_link(other.id, link["token"], password="wrong")

    resolved = manager.resolve_share_link(other.id, link["token"], password="hunter2")
    assert resolved["item_id"] == str(item["id"])


def test_share_link_errors_are_indistinguishable(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"], password="secret")
    wrong_password_message = None
    missing_token_message = None
    with patch.object(encryption, "verify_password", return_value=False):
        try:
            manager.resolve_share_link(other.id, link["token"], password="nope")
        except StashPermissionError as exc:
            wrong_password_message = str(exc)
    try:
        manager.resolve_share_link(other.id, "bogus-token")
    except StashPermissionError as exc:
        missing_token_message = str(exc)
    assert wrong_password_message == missing_token_message


def test_revoked_share_link_cannot_be_redeemed(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"])
    manager.revoke_share_link(owner.id, link["id"])
    with pytest.raises(StashPermissionError):
        manager.resolve_share_link(other.id, link["token"])


def test_expired_share_link_cannot_be_redeemed(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    now = int(time.time() * 1000)
    # Creating a link with a past expiry is rejected up front.
    with pytest.raises(StashValidationError):
        manager.create_share_link(owner.id, item["id"], expires_at=now - 60_000)

    # A link that expires after creation can no longer be redeemed.
    link = manager.create_share_link(owner.id, item["id"], expires_at=now + 60_000)
    manager._db.execute(
        "UPDATE stash_share_links SET expires_at = ? WHERE id = ?",
        (now - 1, int(link["id"])),
    )
    with pytest.raises(StashPermissionError):
        manager.resolve_share_link(other.id, link["token"])


def test_share_link_cannot_grant_privileged_permissions(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    with pytest.raises(StashValidationError):
        manager.create_share_link(owner.id, item["id"], permissions=["share"])
    with pytest.raises(StashValidationError):
        manager.create_share_link(owner.id, item["id"], permissions=["delete"])


def test_non_owner_cannot_create_share_link(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    with pytest.raises(StashPermissionError):
        manager.create_share_link(other.id, item["id"])


def test_list_share_links_never_exposes_token(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    manager.create_share_link(owner.id, item["id"], password="pw")
    links = manager.list_share_links(owner.id, item["id"])
    assert len(links) == 1
    assert "token" not in links[0]
    assert links[0]["password_protected"] is True


def test_share_link_access_count_increments(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"])
    manager.resolve_share_link(other.id, link["token"])
    manager.resolve_share_link(other.id, link["token"])
    links = manager.list_share_links(owner.id, item["id"])
    assert links[0]["access_count"] == 2


def test_share_link_on_deleted_item_is_not_found(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"])
    manager.delete_item(owner.id, item["id"])
    with pytest.raises(StashNotFoundError):
        manager.resolve_share_link(other.id, link["token"])


def test_share_link_recipient_can_create_and_redeem_download_ticket(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"])
    ticket = manager.create_share_download_ticket(other.id, link["token"])
    assert ticket["item_id"] == str(item["id"])
    assert ticket["media_object_id"]

    # The recipient (who owns no grant and is not a member) can resolve and
    # redeem the ticket — the share link is their authorization.
    resolved = manager.resolve_download_ticket(other.id, ticket["ticket"])
    assert resolved["media_object_id"] == ticket["media_object_id"]
    redeemed = manager.redeem_download_ticket(other.id, ticket["ticket"])
    assert redeemed["item_id"] == str(item["id"])


def test_share_download_ticket_requires_download_permission(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(
        owner.id, item["id"], permissions=["view_metadata", "preview"]
    )
    with pytest.raises(StashPermissionError):
        manager.create_share_download_ticket(other.id, link["token"])


def test_share_download_ticket_requires_password(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"], password="hunter2")
    with pytest.raises(StashPermissionError):
        manager.create_share_download_ticket(other.id, link["token"])
    ticket = manager.create_share_download_ticket(
        other.id, link["token"], password="hunter2"
    )
    assert ticket["ticket"]


def test_revoked_share_link_invalidates_download_ticket(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    item = _file_item(owner, manager, media_manager)

    link = manager.create_share_link(owner.id, item["id"])
    ticket = manager.create_share_download_ticket(other.id, link["token"])

    manager.revoke_share_link(owner.id, link["id"])
    # The ticket token is still valid, but the share link it was issued from is
    # revoked, so redemption must fail rather than honoring a stale ticket.
    with pytest.raises(StashPermissionError):
        manager.resolve_download_ticket(other.id, ticket["ticket"])
