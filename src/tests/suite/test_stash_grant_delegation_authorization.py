"""Grant-time authorization: a grantor may only delegate held permissions.

The item ``grant()`` and ``create_share_link()`` entry points authorize the
caller with ``_authorize(..., "share", mutation=True)``. On a server-scoped
item that check also passes for a grantee who was given *only* ``share``,
because ``_grant_permissions`` reports ``share`` as held. Before the fix,
nothing re-checked the permissions being minted: a share-only grantee could
grant themselves (or another member) ``delete``/``manage_versions``, or issue
a ``download`` share link and redeem it for bytes they could not otherwise
fetch. That is an unauthorized grant created by a non-owner -- a privilege
escalation, not mere privilege *display*.

The fix (``StashManagerBase._ensure_delegable_permissions``) re-runs the item
authorization boundary for every delegated permission and aborts if any check
fails. These tests prove the escalation is closed **and** that legitimate
delegation by a grantee who actually holds the permission still works, so the
guard is not vacuous.
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.schema import create_tables as create_stash_tables
from src.core.suite.stash.manager import StashPermissionError

pytestmark = pytest.mark.unit

_SERVER_ID = 4242


class _FakeServers:
    """Deterministic server boundary: no cross-test global caches."""

    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.members = {owner_id}

    def add_member(self, user_id):
        self.members.add(user_id)

    def get_member(self, server_id, user_id):
        if int(server_id) != _SERVER_ID or int(user_id) not in self.members:
            return None
        return object()

    def get_server(self, server_id, user_id):
        if int(server_id) != _SERVER_ID or int(user_id) not in self.members:
            return None

        class _Server:
            owner_id = self.owner_id

        return _Server()

    def has_permission(self, user_id, server_id, permission):
        return False


def _manager(db, servers, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=servers, media=media_manager)


def _server_item(owner, servers, media_manager, manager):
    uploaded = media_manager.upload_file(
        user_id=owner.id,
        file_data=b"bytes",
        filename="grant.txt",
        content_type="text/plain",
    )
    item = manager.create_item(
        owner.id,
        item_type="file",
        title="Grant item",
        scope="server",
        server_id=_SERVER_ID,
        media_object_id=str(uploaded.file_id),
    )
    return item


def _grant_rows(db, item_id, subject_id):
    return db.fetch_all(
        "SELECT id, permissions_json, revoked_at FROM stash_grants "
        "WHERE item_id = ? AND subject_id = ?",
        (int(item_id), str(subject_id)),
    )


def test_share_only_grantee_cannot_mint_privileged_grant(db, two_users, media_manager):
    """A share-only grantee must not delegate permissions they do not hold."""
    owner, delegate = two_users
    servers = _FakeServers(owner.id)
    servers.add_member(delegate.id)
    manager = _manager(db, servers, media_manager)
    item = _server_item(owner, servers, media_manager, manager)

    # The owner gives the delegate exactly one permission: ``share``. No
    # delete / manage_versions / download authority is granted.
    manager.grant(owner.id, int(item["id"]), delegate.id, ["share"])
    assert _grant_rows(db, item["id"], delegate.id)

    # Pre-fix: the delegate's "share" authority let them create this grant.
    with pytest.raises(StashPermissionError):
        manager.grant(
            delegate.id,
            int(item["id"]),
            delegate.id,
            ["delete", "manage_versions"],
        )

    # Fail closed: no escalated grant row was created for the delegate.
    rows = _grant_rows(db, item["id"], delegate.id)
    assert len(rows) == 1
    assert "delete" not in rows[0]["permissions_json"]
    assert "manage_versions" not in rows[0]["permissions_json"]


def test_share_only_grantee_cannot_mint_download_share_link(
    db, two_users, media_manager
):
    """A share-only grantee must not mint a link granting bytes they cannot read."""
    owner, delegate = two_users
    servers = _FakeServers(owner.id)
    servers.add_member(delegate.id)
    manager = _manager(db, servers, media_manager)
    item = _server_item(owner, servers, media_manager, manager)

    manager.grant(owner.id, int(item["id"]), delegate.id, ["share"])

    # The delegate cannot download the item directly ...
    with pytest.raises(StashPermissionError):
        manager.create_download_ticket(delegate.id, int(item["id"]))

    # ... so they must not be able to create a link that grants download.
    with pytest.raises(StashPermissionError):
        manager.create_share_link(
            delegate.id, int(item["id"]), permissions=["download"]
        )


def test_grantee_holding_permission_can_still_delegate_it(
    db, three_users, media_manager
):
    """Non-vacuous: holding a permission still authorizes delegating it."""
    owner, delegate, target = three_users
    servers = _FakeServers(owner.id)
    servers.add_member(delegate.id)
    servers.add_member(target.id)
    manager = _manager(db, servers, media_manager)
    item = _server_item(owner, servers, media_manager, manager)

    # The delegate genuinely holds ``delete`` this time (plus ``share`` to
    # reach the grant entry point).
    manager.grant(owner.id, int(item["id"]), delegate.id, ["delete", "share"])
    granted = manager.grant(delegate.id, int(item["id"]), target.id, ["delete"])
    assert granted["permissions"] == ["delete"]
    assert _grant_rows(db, item["id"], target.id)

    # The delegated grant actually authorizes the target (view_metadata is
    # implied by delete), proving the guard did not merely disable grants.
    assert manager.get_item(target.id, int(item["id"])) is not None


def test_owner_can_still_grant_privileged_permissions(db, two_users, media_manager):
    """Non-vacuous: the owner's privileged grants are unaffected."""
    owner, target = two_users
    servers = _FakeServers(owner.id)
    servers.add_member(target.id)
    manager = _manager(db, servers, media_manager)
    item = _server_item(owner, servers, media_manager, manager)

    granted = manager.grant(
        owner.id, int(item["id"]), target.id, ["delete", "manage_versions"]
    )
    assert granted["permissions"] == ["delete", "manage_versions"]
    assert len(_grant_rows(db, item["id"], target.id)) == 1
