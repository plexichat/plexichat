"""Tests for the PlexiStash authorization vocabulary helpers.

``permissions.py`` was previously a misleading contract stub; it now exposes the
canonical permission names plus pure validation helpers that mirror the rules
enforced by ``StashManager``. These tests pin the contract so the vocabulary
cannot drift from the enforcement.
"""

import pytest

from src.core.suite.stash.permissions import (
    PERSONAL_DELEGABLE_PERMISSIONS,
    STASH_PERMISSION_NAMES,
    VALID_SCOPES,
    delegable_on_personal,
    is_valid_permission,
    non_delegable_on_personal,
    validate_permissions,
)


class TestPermissionVocabulary:
    def test_all_manager_permissions_are_known(self):
        # The manager enforces exactly these names (STASH_PERMISSIONS).
        for permission in STASH_PERMISSION_NAMES:
            assert is_valid_permission(permission)

    def test_unknown_permission_is_rejected(self):
        assert not is_valid_permission("admin")
        assert not is_valid_permission("")
        assert not is_valid_permission("view")

    def test_valid_scopes(self):
        assert set(VALID_SCOPES) == {"personal", "server"}


class TestValidatePermissions:
    def test_normalizes_and_dedupes(self):
        assert validate_permissions(["download", "view_metadata", "download"]) == (
            "download",
            "view_metadata",
        )

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            validate_permissions(["view_metadata", "sudo"])

    def test_accepts_empty(self):
        assert validate_permissions([]) == ()


class TestPersonalDelegation:
    def test_privileged_permissions_are_not_delegable(self):
        # Personal-item owners must not delegate share/delete/manage_versions:
        # doing so would let a grantee delete or reshare the owner's item.
        privileged = {"share", "delete", "manage_versions"}
        assert not (privileged & PERSONAL_DELEGABLE_PERMISSIONS)

    def test_delegable_on_personal_filters_privileged(self):
        result = delegable_on_personal(
            ["view_metadata", "download", "edit", "share", "delete"]
        )
        assert set(result) == {"view_metadata", "download", "edit"}

    def test_non_delegable_on_personal_returns_privileged(self):
        result = non_delegable_on_personal(
            ["view_metadata", "share", "manage_versions"]
        )
        assert set(result) == {"share", "manage_versions"}


class TestServerScopedGrantEnforcement:
    """Agent 30 H2: explicit grants on server-scoped items are enforced.

    Grants on server-scoped items were accepted and displayed but never
    consulted by ``_authorize`` for mutations, so every grantee edit/move
    call failed with 403 despite the permission UI showing access.
    """

    def _manager(self, db, server_manager, media_manager):
        from src.core.suite.stash import StashManager
        from src.core.suite.stash.schema import (
            create_tables as create_stash_tables,
        )

        create_stash_tables(db)
        return StashManager(db, servers_module=server_manager, media=media_manager)

    def test_grantee_can_mutate_server_item(
        self, db, two_users, server_manager, media_manager
    ):
        owner, grantee = two_users
        server = server_manager.create_server(owner.id, "Enforcement server")
        server_manager.add_member(server.id, grantee.id)
        manager = self._manager(db, server_manager, media_manager)
        item = manager.create_item(
            owner.id,
            item_type="note",
            title="Server note",
            scope="server",
            server_id=server.id,
        )
        manager.grant(owner.id, int(item["id"]), grantee.id, ["edit"])

        updated = manager.update_item(
            grantee.id, int(item["id"]), title="Edited by grantee"
        )
        assert updated["title"] == "Edited by grantee"

    def test_member_without_grant_cannot_mutate_server_item(
        self, db, two_users, server_manager, media_manager
    ):
        from src.core.suite.stash.manager import StashPermissionError

        owner, member = two_users
        server = server_manager.create_server(owner.id, "Boundary server")
        server_manager.add_member(server.id, member.id)
        manager = self._manager(db, server_manager, media_manager)
        item = manager.create_item(
            owner.id,
            item_type="note",
            title="Server note",
            scope="server",
            server_id=server.id,
        )
        import pytest

        with pytest.raises(StashPermissionError):
            manager.update_item(member.id, int(item["id"]), title="Nope")
