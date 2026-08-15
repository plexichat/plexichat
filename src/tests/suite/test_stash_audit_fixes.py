"""Regression tests for PlexiStash audit fixes.

Covers:
- PX-1: the retention cleanup purges file-backed items (media wired in).
- PX-2: personal-item owners cannot delegate privileged grants.
- PX-3: a non-owner editor (server manager) can re-point media.
- PX-4: server admins/moderators may restore deleted server items.
- PX-5: ``get_item`` returns grants only to the owner / server admin.
- PX-9: download tickets expose ``media_object_id``.
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.schema import create_tables as create_stash_tables
from src.core.suite.stash.manager import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)


def _manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def test_personal_grant_cannot_delegate_privileged_permissions(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _manager(db, server_manager, media_manager)
    item = manager.create_item(owner.id, item_type="note", title="Shared note")

    # Safe permissions are allowed on personal items.
    manager.grant(owner.id, int(item["id"]), other.id, ["view_metadata", "download"])

    # Privileged mutations (delete/share/manage_versions) must be refused.
    for privileged in (["delete"], ["share"], ["manage_versions"], ["delete", "edit"]):
        with pytest.raises(StashValidationError):
            manager.grant(owner.id, int(item["id"]), other.id, privileged)


def test_server_grant_may_delegate_privileged_permissions(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    server = server_manager.create_server(owner.id, "Stash server")
    server_manager.add_member(server.id, other.id)
    manager = _manager(db, server_manager, media_manager)
    item = manager.create_item(
        owner.id,
        item_type="note",
        title="Server note",
        scope="server",
        server_id=server.id,
    )
    # Server-scoped items may delegate the full permission set.
    manager.grant(owner.id, int(item["id"]), other.id, ["delete", "share"])


def test_non_owner_editor_can_repoint_media(
    db, two_users, server_manager, media_manager
):
    owner, editor = two_users
    manager = _manager(db, server_manager, media_manager)

    file_one = media_manager.upload_file(
        user_id=owner.id, file_data=b"v1", filename="v1.txt", content_type="text/plain"
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Doc",
        scope="personal",
        media_object_id=str(file_one.file_id),
    )

    # A grantee with edit permission (not the owner) uploads a new file and
    # re-points the item at it. The fix resolves media access against the
    # editor, not the owner, so the uploader check uses the editor.
    manager.grant(owner.id, int(item["id"]), editor.id, ["edit"])
    file_two = media_manager.upload_file(
        user_id=editor.id,
        file_data=b"v2",
        filename="v2.txt",
        content_type="text/plain",
    )
    updated = manager.update_item(
        editor.id, int(item["id"]), media_object_id=str(file_two.file_id)
    )
    assert updated["media_object_id"] == str(file_two.file_id)


def test_get_item_exposes_grants_only_to_owner(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _manager(db, server_manager, media_manager)
    item = manager.create_item(owner.id, item_type="note", title="Shared note")
    manager.grant(owner.id, int(item["id"]), other.id, ["view_metadata"])

    owner_view = manager.get_item(owner.id, int(item["id"]))
    assert isinstance(owner_view["grants"], list)
    assert any(g["subject_id"] == str(other.id) for g in owner_view["grants"])

    # A grantee must not see the grant list (no enumeration of sharees).
    grantee_view = manager.get_item(other.id, int(item["id"]))
    assert "grants" not in grantee_view


def test_restore_allows_server_admin_via_permission(
    db, two_users, server_manager, media_manager, monkeypatch
):
    owner, admin = two_users
    server = server_manager.create_server(owner.id, "Stash server")
    server_manager.add_member(server.id, admin.id)
    manager = _manager(db, server_manager, media_manager)
    item = manager.create_item(
        owner.id,
        item_type="note",
        title="Server note",
        scope="server",
        server_id=server.id,
    )
    manager.delete_item(owner.id, int(item["id"]))

    # Pretend the admin has the server.admin permission (fixture has no such
    # role by default); the fix consults server.admin / server.moderator too.
    monkeypatch.setattr(
        server_manager,
        "has_permission",
        lambda uid, sid, perm: (
            uid == admin.id and perm in ("server.admin", "server.moderator")
        ),
    )
    restored = manager.restore_item(admin.id, int(item["id"]))
    assert restored["title"] == "Server note"


def test_download_ticket_exposes_media_object_id(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _manager(db, server_manager, media_manager)
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"bytes",
        filename="f.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id, item_type="file", title="File", media_object_id=str(uploaded.file_id)
    )
    ticket = manager.create_download_ticket(owner.id, int(item["id"]))
    assert ticket["media_object_id"] == str(uploaded.file_id)


def test_resolve_download_ticket_does_not_consume_ticket(
    db, two_users, server_manager, media_manager
):
    """Resolving a ticket (for URL signing) must not mark it redeemed.

    The route resolves + signs the media URL *before* consuming the ticket so
    a transient media-storage failure cannot burn a one-time ticket with
    nothing returned. Resolution must leave the ticket redeemable.
    """
    owner, _ = two_users
    manager = _manager(db, server_manager, media_manager)
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"bytes",
        filename="f.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id, item_type="file", title="File", media_object_id=str(uploaded.file_id)
    )
    ticket = manager.create_download_ticket(owner.id, int(item["id"]))

    resolved = manager.resolve_download_ticket(owner.id, ticket["ticket"])
    assert resolved["item_id"] == str(item["id"])
    assert resolved["media_object_id"] == str(uploaded.file_id)

    # The ticket is still valid after resolution (not yet redeemed).
    redeemed = manager.redeem_download_ticket(owner.id, ticket["ticket"])
    assert redeemed["item_id"] == str(item["id"])

    # A second redemption still fails (single-use).
    with pytest.raises(StashPermissionError):
        manager.redeem_download_ticket(owner.id, ticket["ticket"])


def test_cleanup_purges_file_backed_items(db, two_users, server_manager, media_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager, media_manager)
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"purge-me",
        filename="f.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id, item_type="file", title="File", media_object_id=str(uploaded.file_id)
    )
    manager.delete_item(owner.id, int(item["id"]))

    # Force the item past retention so it is eligible for hard purge.
    db.execute("UPDATE stash_items SET updated_at = 0 WHERE id = ?", (int(item["id"]),))

    purged = manager.purge_deleted_items(retention_days=30)
    assert purged >= 1
    with pytest.raises(StashNotFoundError):
        manager.get_item(owner.id, int(item["id"]))


# ---------------------------------------------------------------------------
# PX-6: non-owner grantee permission-surface audit (share / delete /
# manage_versions). A personal-item grantee can only ever hold non-privileged
# permissions, and server-scoped mutations are gated on server management, so a
# grantee (even one holding a privileged grant) must never be able to share,
# delete, or manage versions of an item they do not own / manage.
# ---------------------------------------------------------------------------


def test_personal_grantee_cannot_share_delete_or_manage_versions(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _manager(db, server_manager, media_manager)
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"v1",
        filename="v1.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Doc",
        scope="personal",
        media_object_id=str(uploaded.file_id),
    )
    # Editor can mutate content but holds no privileged permission.
    manager.grant(owner.id, int(item["id"]), other.id, ["edit"])

    with pytest.raises(StashPermissionError):
        manager.delete_item(other.id, int(item["id"]))
    with pytest.raises(StashPermissionError):
        manager.grant(other.id, int(item["id"]), owner.id, ["view_metadata"])
    with pytest.raises(StashPermissionError):
        manager.create_version(
            other.id,
            int(item["id"]),
            media_object_id=str(uploaded.file_id),
            content_type="text/plain",
        )


def test_server_grantee_privileged_grant_is_not_usable_without_management(
    db, two_users, server_manager, media_manager
):
    owner, member = two_users
    server = server_manager.create_server(owner.id, "Stash server")
    server_manager.add_member(server.id, member.id)
    manager = _manager(db, server_manager, media_manager)
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"v1",
        filename="v1.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Server Doc",
        scope="server",
        server_id=server.id,
        media_object_id=str(uploaded.file_id),
    )
    # Owner may grant privileged permissions to a member (server scope allows
    # the full set), but the grant must remain inert for non-managers.
    manager.grant(owner.id, int(item["id"]), member.id, ["delete", "share"])

    with pytest.raises(StashPermissionError):
        manager.delete_item(member.id, int(item["id"]))
    with pytest.raises(StashPermissionError):
        manager.grant(member.id, int(item["id"]), owner.id, ["view_metadata"])
    with pytest.raises(StashPermissionError):
        manager.create_version(
            member.id,
            int(item["id"]),
            media_object_id=str(uploaded.file_id),
            content_type="text/plain",
        )


def test_regrant_returns_same_id_and_is_revocable(
    db, two_users, server_manager, media_manager
):
    """Re-granting to an existing subject must upsert, not insert.

    The first grant's id is the canonical row id; a second grant to the same
    subject must return that same id so ``revoke_grant`` targets the real row
    instead of a freshly-generated phantom id (which previously 404'd).
    """
    owner, other = two_users
    manager = _manager(db, server_manager, media_manager)
    item = manager.create_item(owner.id, item_type="note", title="Note")

    first = manager.grant(owner.id, int(item["id"]), other.id, ["view_metadata"])
    second = manager.grant(
        owner.id, int(item["id"]), other.id, ["view_metadata", "download"]
    )

    assert second["id"] == first["id"]
    assert "download" in second["permissions"]

    # Revoking via the returned id must actually remove the grantee's access.
    manager.revoke_grant(owner.id, int(second["id"]))
    with pytest.raises(StashPermissionError):
        manager.get_item(other.id, int(item["id"]))


def test_link_item_rejects_unsafe_urls(db, two_users, server_manager, media_manager):
    """Link items must only accept absolute http(s) URLs (SSRF/XSS guard)."""
    owner, _ = two_users
    manager = _manager(db, server_manager, media_manager)
    for bad in (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "relative/path",
        "data:text/html,x",
    ):
        with pytest.raises(StashValidationError):
            manager.create_item(
                owner.id, item_type="link", title="L", metadata={"url": bad}
            )
    with pytest.raises(StashValidationError):
        manager.create_item(
            owner.id, item_type="link", title="L", metadata={"url": 123}
        )


def test_link_item_accepts_http_urls_and_updates(
    db, two_users, server_manager, media_manager
):
    """http(s) link URLs are accepted, and the same rule applies on update."""
    owner, _ = two_users
    manager = _manager(db, server_manager, media_manager)
    item = manager.create_item(
        owner.id, item_type="link", title="L", metadata={"url": "https://example.com/a"}
    )
    assert item["metadata"]["url"] == "https://example.com/a"

    updated = manager.update_item(
        owner.id, int(item["id"]), metadata={"url": "http://example.org/b"}
    )
    assert updated["metadata"]["url"] == "http://example.org/b"

    with pytest.raises(StashValidationError):
        manager.update_item(
            owner.id, int(item["id"]), metadata={"url": "javascript:alert(1)"}
        )


def test_server_manager_may_delete_server_item(
    db, two_users, server_manager, media_manager, monkeypatch
):
    owner, manager_user = two_users
    server = server_manager.create_server(owner.id, "Stash server")
    server_manager.add_member(server.id, manager_user.id)
    manager = _manager(db, server_manager, media_manager)
    item = manager.create_item(
        owner.id,
        item_type="note",
        title="Server note",
        scope="server",
        server_id=server.id,
    )
    # A server admin/moderator may perform lifecycle mutations even though they
    # are not the item owner.
    monkeypatch.setattr(
        server_manager,
        "has_permission",
        lambda uid, sid, perm: (
            uid == manager_user.id and perm in ("server.admin", "server.moderator")
        ),
    )
    manager.delete_item(manager_user.id, int(item["id"]))
    with pytest.raises(StashNotFoundError):
        manager.get_item(owner.id, int(item["id"]))
