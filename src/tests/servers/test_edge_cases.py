"""
Tests for edge cases and error handling.
"""

import pytest


class TestServerEdgeCases:
    """Edge cases for server operations."""

    def test_get_nonexistent_server(self, users):
        """Test getting nonexistent server."""
        owner, _, _, _, servers = users

        result = servers.get_server(999999999, owner.id)

        assert result is None

    def test_update_nonexistent_server(self, users):
        """Test updating nonexistent server."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.ServerNotFoundError):
            servers.update_server(owner.id, 999999999, name="Test")

    def test_delete_nonexistent_server(self, users):
        """Test deleting nonexistent server."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.ServerNotFoundError):
            servers.delete_server(owner.id, 999999999)


class TestChannelEdgeCases:
    """Edge cases for channel operations."""

    def test_get_nonexistent_channel(self, users):
        """Test getting nonexistent channel."""
        owner, _, _, _, servers = users

        result = servers.get_channel(999999999, owner.id)

        assert result is None

    def test_update_nonexistent_channel(self, users):
        """Test updating nonexistent channel."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.ChannelNotFoundError):
            servers.update_channel(owner.id, 999999999, name="test")

    def test_delete_nonexistent_channel(self, users):
        """Test deleting nonexistent channel."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.ChannelNotFoundError):
            servers.delete_channel(owner.id, 999999999)

    def test_create_channel_invalid_category(self, fresh_server):
        """Test creating channel with invalid category."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.CategoryNotFoundError):
            servers.create_channel(
                user_id=owner.id,
                server_id=server.id,
                name="test",
                category_id=999999999
            )


class TestRoleEdgeCases:
    """Edge cases for role operations."""

    def test_get_nonexistent_role(self, users):
        """Test getting nonexistent role."""
        owner, _, _, _, servers = users

        result = servers.get_role(999999999, owner.id)

        assert result is None

    def test_update_nonexistent_role(self, users):
        """Test updating nonexistent role."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.RoleNotFoundError):
            servers.update_role(owner.id, 999999999, name="test")

    def test_delete_nonexistent_role(self, users):
        """Test deleting nonexistent role."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.RoleNotFoundError):
            servers.delete_role(owner.id, 999999999)

    def test_assign_nonexistent_role(self, server_with_members):
        """Test assigning nonexistent role."""
        server, owner, _, member_user, _, _, servers = server_with_members

        with pytest.raises(servers.RoleNotFoundError):
            servers.assign_role(owner.id, server.id, member_user.id, 999999999)


class TestMemberEdgeCases:
    """Edge cases for member operations."""

    def test_kick_nonexistent_member(self, fresh_server, base_users):
        """Test kicking nonexistent member."""
        server, owner, servers = fresh_server
        _, _, _, outsider, _, _, _ = base_users

        with pytest.raises(servers.MemberNotFoundError):
            servers.kick_member(owner.id, server.id, outsider.id)

    def test_ban_nonexistent_member(self, fresh_server, base_users):
        """Test banning non-member (should work - can ban non-members)."""
        server, owner, servers = fresh_server
        _, _, _, outsider, _, _, _ = base_users

        # Banning non-member should work (preemptive ban)
        ban = servers.ban_member(owner.id, server.id, outsider.id)
        assert ban is not None

    def test_update_nonexistent_member(self, fresh_server, base_users):
        """Test updating nonexistent member."""
        server, owner, servers = fresh_server
        _, _, _, outsider, _, _, _ = base_users

        with pytest.raises(servers.MemberNotFoundError):
            servers.update_member(owner.id, server.id, outsider.id, nickname="Test")


class TestInviteEdgeCases:
    """Edge cases for invite operations."""

    def test_create_invite_nonexistent_channel(self, users):
        """Test creating invite for nonexistent channel."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.ChannelNotFoundError):
            servers.create_invite(owner.id, 999999999)

    def test_use_revoked_invite(self, server_with_channels, base_users):
        """Test using revoked invite."""
        server, owner, _, _, outsider, general, _, _, _, servers = server_with_channels

        invite = servers.create_invite(owner.id, general.id)
        servers.delete_invite(owner.id, invite.code)

        with pytest.raises(servers.InviteNotFoundError):
            servers.use_invite(outsider.id, invite.code)


class TestPermissionEdgeCases:
    """Edge cases for permission operations."""

    def test_get_permissions_nonexistent_server(self, users):
        """Test getting permissions for nonexistent server."""
        owner, _, _, _, servers = users

        perms = servers.get_permissions(owner.id, 999999999)

        assert perms == {}

    def test_set_override_nonexistent_channel(self, users):
        """Test setting override for nonexistent channel."""
        owner, _, _, _, servers = users

        with pytest.raises(servers.ChannelNotFoundError):
            servers.set_channel_override(
                owner.id, 999999999, "role", 1,
                deny={"messages.send": True}
            )


class TestValidationEdgeCases:
    """Edge cases for input validation."""

    def test_server_name_with_special_chars(self, users):
        """Test server name with special characters."""
        owner, _, _, _, servers = users

        server = servers.create_server(
            owner_id=owner.id,
            name="Test Server!@#$%"
        )

        assert server is not None
        assert "Test Server" in server.name

    def test_channel_name_normalization(self, fresh_server):
        """Test channel name normalization."""
        server, owner, servers = fresh_server

        channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="  My Channel  "
        )

        assert channel.name == "my-channel"

    def test_role_name_with_spaces(self, fresh_server):
        """Test role name with spaces."""
        server, owner, servers = fresh_server

        role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="  Super Admin  "
        )

        assert role.name == "Super Admin"


class TestConcurrentOperations:
    """Tests for concurrent/race condition scenarios."""

    def test_delete_server_with_members(self, server_with_members):
        """Test deleting server with members."""
        server, owner, _, _, _, _, servers = server_with_members

        result = servers.delete_server(owner.id, server.id)

        assert result is True

    def test_delete_channel_with_overrides(self, server_with_channels):
        """Test deleting channel with permission overrides."""
        server, owner, _, member_user, _, general, _, _, _, servers = server_with_channels

        # Add override
        servers.set_channel_override(
            owner.id, general.id, "member", member_user.id,
            deny={"messages.send": True}
        )

        # Delete channel
        result = servers.delete_channel(owner.id, general.id)

        assert result is True

    def test_delete_role_with_overrides(self, server_with_channels):
        """Test deleting role that has channel overrides."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels

        # Create role
        role = servers.create_role(owner.id, server.id, name="Test Role")

        # Add override for role
        servers.set_channel_override(
            owner.id, general.id, "role", role.id,
            deny={"messages.send": True}
        )

        # Delete role
        result = servers.delete_role(owner.id, role.id)

        assert result is True


class TestOwnerProtection:
    """Tests for owner protection."""

    def test_cannot_kick_owner(self, server_with_members):
        """Test that owner cannot be kicked."""
        server, owner, admin_user, _, _, _, servers = server_with_members

        with pytest.raises(servers.CannotModifyOwnerError):
            servers.kick_member(admin_user.id, server.id, owner.id)

    def test_cannot_ban_owner(self, server_with_members):
        """Test that owner cannot be banned."""
        server, owner, admin_user, _, _, _, servers = server_with_members

        with pytest.raises(servers.CannotModifyOwnerError):
            servers.ban_member(admin_user.id, server.id, owner.id)

    def test_owner_cannot_leave_without_transfer(self, fresh_server):
        """Test that owner cannot leave without transferring."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.OwnerCannotLeaveError):
            servers.remove_member(owner.id, server.id)
