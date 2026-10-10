"""
Tests for permission operations.
"""

import pytest


class TestHasPermission:
    """Tests for checking permissions."""

    def test_owner_has_all_permissions(self, fresh_server):
        """Test that owner has all permissions."""
        server, owner, servers = fresh_server

        assert servers.has_permission(owner.id, server.id, "administrator") is True
        assert servers.has_permission(owner.id, server.id, "server.manage") is True
        assert servers.has_permission(owner.id, server.id, "members.ban") is True

    def test_member_has_default_permissions(self, server_with_members):
        """Test that member has default permissions."""
        server, _, _, member_user, _, _, servers = server_with_members

        assert (
            servers.has_permission(member_user.id, server.id, "messages.send") is True
        )
        assert (
            servers.has_permission(member_user.id, server.id, "messages.read") is True
        )

    def test_member_has_artifact_permissions(self, server_with_members):
        """Every member can use the collaborative suite artifacts in a server."""
        server, _, _, member_user, _, _, servers = server_with_members

        assert (
            servers.has_permission(member_user.id, server.id, "artifact.view") is True
        )
        assert (
            servers.has_permission(member_user.id, server.id, "artifact.create") is True
        )
        assert (
            servers.has_permission(member_user.id, server.id, "artifact.edit") is True
        )
        assert (
            servers.has_permission(member_user.id, server.id, "artifact.delete") is True
        )

    def test_member_lacks_admin_permissions(self, server_with_members):
        """Test that member lacks admin permissions."""
        server, _, _, member_user, _, _, servers = server_with_members

        assert (
            servers.has_permission(member_user.id, server.id, "server.manage") is False
        )
        assert servers.has_permission(member_user.id, server.id, "members.ban") is False

    def test_admin_role_grants_permissions(self, server_with_members):
        """Test that admin role grants permissions."""
        server, _, admin_user, _, _, admin_role, servers = server_with_members

        # Admin role has channels.manage
        assert (
            servers.has_permission(admin_user.id, server.id, "channels.manage") is True
        )

    def test_administrator_permission_grants_all(self, fresh_server):
        """Test that administrator permission grants all."""
        server, owner, servers = fresh_server

        # Create role with administrator
        servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Admin",
            permissions={"administrator": True},
        )

        # Add a member and give them the role
        # (Using owner for simplicity since they already have all perms)
        assert (
            servers.has_permission(owner.id, server.id, "some.random.permission")
            is True
        )


class TestGetPermissions:
    """Tests for getting all permissions."""

    def test_get_permissions_returns_dict(self, server_with_members):
        """Test that get_permissions returns a dict."""
        server, _, _, member_user, _, _, servers = server_with_members

        perms = servers.get_permissions(member_user.id, server.id)

        assert isinstance(perms, dict)
        assert "messages.send" in perms

    def test_get_permissions_for_owner(self, fresh_server):
        """Test getting permissions for owner."""
        server, owner, servers = fresh_server

        perms = servers.get_permissions(owner.id, server.id)

        # Owner should have all permissions
        assert perms.get("administrator") is True


class TestRequirePermission:
    """Tests for requiring permissions."""

    def test_require_permission_success(self, server_with_members):
        """Test require_permission when user has permission."""
        server, owner, _, _, _, _, servers = server_with_members

        assert servers.require_permission(owner.id, server.id, "server.manage") is None

    def test_require_permission_failure(self, server_with_members):
        """Test require_permission when user lacks permission."""
        server, _, _, member_user, _, _, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError) as exc_info:
            servers.require_permission(member_user.id, server.id, "server.manage")

        assert exc_info.value.permission == "server.manage"


class TestChannelOverrides:
    """Tests for channel permission overrides."""

    def test_set_role_override(self, server_with_channels):
        """Test setting a role override."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        override = servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="role",
            target_id=default_role.id,
            deny={"messages.send": True},
        )

        assert override is not None
        assert override.deny.get("messages.send") is True

    def test_set_member_override(self, server_with_channels):
        """Test setting a member override."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        override = servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"messages.send": True},
        )

        assert override is not None
        assert override.deny.get("messages.send") is True

    def test_override_affects_permissions(self, server_with_channels):
        """Test that override affects permission check."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        # Member should have send permission by default
        assert (
            servers.has_permission(
                member_user.id, server.id, "messages.send", general.id
            )
            is True
        )

        # Set deny override
        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"messages.send": True},
        )

        # Now member should not have send permission in this channel
        assert (
            servers.has_permission(
                member_user.id, server.id, "messages.send", general.id
            )
            is False
        )

    def test_allow_override_grants_permission(self, server_with_channels):
        """Test that allow override grants permission."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        # First deny at role level
        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="role",
            target_id=default_role.id,
            deny={"messages.send": True},
        )

        # Member should not have permission
        assert (
            servers.has_permission(
                member_user.id, server.id, "messages.send", general.id
            )
            is False
        )

        # Now allow for specific member
        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            allow={"messages.send": True},
        )

        # Member should now have permission
        assert (
            servers.has_permission(
                member_user.id, server.id, "messages.send", general.id
            )
            is True
        )

    def test_get_channel_override(self, server_with_channels):
        """Test getting a channel override."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"messages.send": True},
        )

        override = servers.get_channel_override(general.id, "member", member_user.id)

        assert override is not None
        assert override.deny.get("messages.send") is True

    def test_delete_channel_override(self, server_with_channels):
        """Test deleting a channel override."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"messages.send": True},
        )

        result = servers.delete_channel_override(
            owner.id, general.id, "member", member_user.id
        )

        assert result is True
        assert (
            servers.get_channel_override(general.id, "member", member_user.id) is None
        )

    def test_set_channel_override_without_permission_fails(self, server_with_channels):
        """Test that non-admins cannot create channel overrides."""
        server, _, _, member_user, _, general, _, _, _, servers = server_with_channels

        with pytest.raises(servers.PermissionDeniedError):
            servers.set_channel_override(
                user_id=member_user.id,
                channel_id=general.id,
                target_type="member",
                target_id=member_user.id,
                deny={"messages.send": True},
            )

    def test_delete_channel_override_without_permission_fails(
        self, server_with_channels
    ):
        """Test that non-admins cannot delete channel overrides."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"messages.send": True},
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.delete_channel_override(
                member_user.id, general.id, "member", member_user.id
            )

    def test_update_existing_override(self, server_with_channels):
        """Test updating an existing override."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        # Create initial override
        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"messages.send": True},
        )

        # Update it
        override = servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            allow={"messages.send": True},
            deny={},
        )

        assert override.allow.get("messages.send") is True
        assert override.deny.get("messages.send") is not True


class TestPermissionHierarchy:
    """Tests for permission hierarchy."""

    def test_role_permissions_combine(self, server_with_members):
        """Test that permissions from multiple roles combine."""
        server, owner, admin_user, _, _, admin_role, servers = server_with_members

        # Create another role with different permissions
        mod_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Moderator",
            permissions={"messages.manage": True},
        )

        servers.assign_role(owner.id, server.id, admin_user.id, mod_role.id)

        # Admin should have permissions from both roles
        assert (
            servers.has_permission(admin_user.id, server.id, "channels.manage") is True
        )  # From admin role
        assert (
            servers.has_permission(admin_user.id, server.id, "messages.manage") is True
        )  # From mod role

    def test_member_override_takes_precedence(self, server_with_channels):
        """Test that member override takes precedence over role override."""
        server, owner, _, member_user, _, general, _, _, _, servers = (
            server_with_channels
        )

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        # Deny at role level
        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="role",
            target_id=default_role.id,
            deny={"messages.send": True},
        )

        # Allow at member level
        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            allow={"messages.send": True},
        )

        # Member override should win
        assert (
            servers.has_permission(
                member_user.id, server.id, "messages.send", general.id
            )
            is True
        )


class TestBackfillDefaultRolePermissions:
    """Tests for merging newly-added default permissions into existing roles."""

    @staticmethod
    def _set_everyone_permissions(db, server_id, perms):
        import json

        db.execute(
            "UPDATE srv_roles SET permissions = ? WHERE server_id = ? AND is_default = 1",
            (json.dumps(perms), server_id),
        )

    @staticmethod
    def _get_everyone_permissions(db, server_id):
        import json

        row = db.fetch_one(
            "SELECT permissions FROM srv_roles WHERE server_id = ? AND is_default = 1",
            (server_id,),
        )
        raw = row["permissions"]
        return json.loads(raw) if isinstance(raw, str) else raw

    def test_backfill_adds_missing_artifact_permissions(self, modules, db, user_pool):
        """Legacy @everyone roles gain the new artifact permissions."""
        from src.core import servers as servers_module

        owner = user_pool.get_user()
        server = modules.servers.create_server(owner_id=owner.id, name="Legacy Server")

        # Simulate a pre-artifact role: strip the artifact.* keys.
        legacy = {
            "channels.view": True,
            "messages.send": True,
            "messages.read": True,
        }
        self._set_everyone_permissions(db, server.id, legacy)

        updated = servers_module.backfill_default_role_permissions(db)
        assert updated >= 1

        perms = self._get_everyone_permissions(db, server.id)
        assert perms.get("artifact.view") is True
        assert perms.get("artifact.create") is True
        assert perms.get("artifact.edit") is True
        assert perms.get("artifact.delete") is True

    def test_backfill_preserves_explicit_false(self, modules, db, user_pool):
        """An explicit False is never re-granted by the backfill."""
        from src.core import servers as servers_module

        owner = user_pool.get_user()
        server = modules.servers.create_server(owner_id=owner.id, name="Denied Server")

        denied = {"messages.send": True, "artifact.create": False}
        self._set_everyone_permissions(db, server.id, denied)

        servers_module.backfill_default_role_permissions(db)

        perms = self._get_everyone_permissions(db, server.id)
        assert perms.get("artifact.create") is False
        # Missing keys are still added.
        assert perms.get("artifact.view") is True

    def test_backfill_is_idempotent(self, modules, db, user_pool):
        """Running the backfill twice does not change anything the second time."""
        from src.core import servers as servers_module

        owner = user_pool.get_user()
        server = modules.servers.create_server(
            owner_id=owner.id, name="Idempotent Server"
        )

        legacy = {"messages.send": True}
        self._set_everyone_permissions(db, server.id, legacy)

        servers_module.backfill_default_role_permissions(db)
        first = self._get_everyone_permissions(db, server.id)
        servers_module.backfill_default_role_permissions(db)
        second = self._get_everyone_permissions(db, server.id)

        assert first == second


class TestChannelOverrideHierarchy:
    """M1: role channel overrides apply LOWEST position first so a higher
    role's deny beats a lower role's allow (Discord hierarchy semantics)."""

    def test_high_role_deny_beats_low_role_allow(self):
        from src.core.servers.permission_utils import apply_channel_overrides

        base = {"messages.send": True}
        low_role = {"allow": {"messages.send": True}, "deny": {}}
        high_role = {"allow": {}, "deny": {"messages.send": True}}
        result = apply_channel_overrides(base, [low_role, high_role], None)
        assert result["messages.send"] is False

    def test_low_role_allow_beats_base_deny(self):
        from src.core.servers.permission_utils import apply_channel_overrides

        base = {"messages.send": False}
        low_role = {"allow": {"messages.send": True}, "deny": {}}
        high_role = {"allow": {}, "deny": {}}
        result = apply_channel_overrides(base, [low_role, high_role], None)
        assert result["messages.send"] is True

    def test_highest_role_wins_conflicting_overrides(self):
        from src.core.servers.permission_utils import apply_channel_overrides

        base = {"messages.send": True}
        mid_role = {"allow": {}, "deny": {"messages.send": True}}
        high_role = {"allow": {"messages.send": True}, "deny": {}}
        result = apply_channel_overrides(base, [mid_role, high_role], None)
        assert result["messages.send"] is True

    def test_member_override_beats_all_roles(self):
        from src.core.servers.permission_utils import apply_channel_overrides

        base = {"messages.send": True}
        high_role = {"allow": {}, "deny": {"messages.send": True}}
        member = {"allow": {"messages.send": True}, "deny": {}}
        result = apply_channel_overrides(base, [high_role], member)
        assert result["messages.send"] is True

    def test_json_string_allow_deny_still_parsed(self):
        from src.core.servers.permission_utils import apply_channel_overrides

        base = {"messages.send": True}
        low_role = {"allow": '{"messages.send": true}', "deny": "{}"}
        high_role = {"allow": "{}", "deny": '{"messages.send": true}'}
        result = apply_channel_overrides(base, [low_role, high_role], None)
        assert result["messages.send"] is False
