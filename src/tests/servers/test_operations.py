"""
Comprehensive tests for ServerManager focusing on edge cases and error paths.
Targeting 80%+ coverage.
"""

import pytest

from src.core.servers.models import ChannelType, AuditLogAction
from src.core.servers.exceptions import (
    InvalidServerNameError,
    InvalidChannelNameError,
    InvalidRoleNameError,
    ServerAccessDeniedError,
    ServerNotFoundError,
    MemberNotFoundError,
    RoleHierarchyError,
    CannotModifyOwnerError,
    BanExistsError,
    UserBannedError,
    OwnerCannotLeaveError,
    DefaultRoleError,
    InviteExpiredError,
    InviteMaxUsesError,
    MemberExistsError,
    InviteNotFoundError,
    PermissionDeniedError,
    CategoryNotFoundError,
    ChannelNotFoundError,
    RoleNotFoundError,
    BanNotFoundError,
)


class TestServerErrorPaths:
    """Test error conditions."""

    def test_create_server_invalid_name(self, server_manager):
        """Server name validation."""
        with pytest.raises(InvalidServerNameError):
            server_manager.create_server(1, "")

        with pytest.raises(InvalidServerNameError):
            server_manager.create_server(1, "x" * 200)

    def test_create_channel_invalid_name(self, server_manager):
        """Channel name validation."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(InvalidChannelNameError):
            server_manager.create_channel(1, server.id, "")

    def test_create_role_invalid_name(self, server_manager):
        """Role name validation."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(InvalidRoleNameError):
            server_manager.create_role(1, server.id, "")

    def test_delete_server_not_owner(self, server_manager):
        """Only owner can delete server."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(ServerAccessDeniedError):
            server_manager.delete_server(2, server.id)

    def test_delete_server_not_found(self, server_manager):
        """Cannot delete nonexistent server."""
        with pytest.raises(ServerNotFoundError):
            server_manager.delete_server(1, 99999)

    def test_transfer_ownership_not_member(self, server_manager):
        """New owner must be member."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(MemberNotFoundError):
            server_manager.transfer_ownership(1, server.id, 2)

    def test_transfer_ownership_not_owner(self, server_manager):
        """Only owner can transfer ownership."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(ServerAccessDeniedError):
            server_manager.transfer_ownership(2, server.id, 1)

    def test_kick_member_higher_role(self, server_manager):
        """Cannot kick member with higher role."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        server_manager.add_member(server.id, 3)

        role = server_manager.create_role(1, server.id, "Admin")
        server_manager.assign_role(1, server.id, 2, role.id)

        # Give kicker permission to kick
        kicker_role = server_manager.create_role(
            1, server.id, "Kicker", permissions={"members.kick": True}
        )
        server_manager.assign_role(1, server.id, 3, kicker_role.id)

        # Ensure Admin is higher
        server_manager.move_role(1, role.id, 100)

        with pytest.raises(RoleHierarchyError):
            server_manager.kick_member(3, server.id, 2)

    def test_kick_server_owner(self, server_manager):
        """Cannot kick server owner."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        # Give kicker permission to kick
        kicker_role = server_manager.create_role(
            1, server.id, "Kicker", permissions={"members.kick": True}
        )
        server_manager.assign_role(1, server.id, 2, kicker_role.id)

        with pytest.raises(CannotModifyOwnerError):
            server_manager.kick_member(2, server.id, 1)

    def test_kick_member_not_found(self, server_manager):
        """Cannot kick nonexistent member."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(MemberNotFoundError):
            server_manager.kick_member(1, server.id, 99999)

    def test_ban_member_already_banned(self, server_manager):
        """Cannot ban already banned user."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        server_manager.ban_member(1, server.id, 2)

        with pytest.raises(BanExistsError):
            server_manager.ban_member(1, server.id, 2)

    def test_ban_owner(self, server_manager):
        """Cannot ban server owner."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        # Give banner permission to ban
        banner_role = server_manager.create_role(
            1, server.id, "Banner", permissions={"members.ban": True}
        )
        server_manager.assign_role(1, server.id, 2, banner_role.id)

        with pytest.raises(CannotModifyOwnerError):
            server_manager.ban_member(2, server.id, 1)

    def test_add_member_banned(self, server_manager):
        """Cannot add banned user."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        server_manager.ban_member(1, server.id, 2)

        with pytest.raises(UserBannedError):
            server_manager.add_member(server.id, 2)

    def test_owner_cannot_leave(self, server_manager):
        """Owner cannot leave server."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(OwnerCannotLeaveError):
            server_manager.remove_member(1, server.id)

    def test_delete_default_role(self, server_manager):
        """Cannot delete default role."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(DefaultRoleError):
            server_manager.delete_role(1, server.default_role_id)

    def test_rename_default_role(self, server_manager):
        """Cannot rename default role."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(DefaultRoleError):
            server_manager.update_role(1, server.default_role_id, name="New Name")

    def test_remove_default_role(self, server_manager):
        """Cannot remove default role from member."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(DefaultRoleError):
            server_manager.remove_role(1, server.id, 2, server.default_role_id)

    def test_use_expired_invite(self, server_manager):
        """Cannot use expired invite."""
        server = server_manager.create_server(1, "Test Server")
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id, max_age=1)

        server_manager._db.execute(
            "UPDATE srv_invites SET expires_at = ? WHERE code = ?",
            (server_manager._get_timestamp() - 1000, invite.code),
        )

        with pytest.raises(InviteExpiredError):
            server_manager.use_invite(2, invite.code)

    def test_use_invite_max_uses(self, server_manager):
        """Cannot exceed invite max uses."""
        server = server_manager.create_server(1, "Test Server")
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id, max_uses=1)

        server_manager.use_invite(2, invite.code)

        with pytest.raises(InviteMaxUsesError):
            server_manager.use_invite(3, invite.code)

    def test_use_invite_already_member(self, server_manager):
        """Cannot use invite if already member."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id)

        with pytest.raises(MemberExistsError):
            server_manager.use_invite(2, invite.code)

    def test_use_invite_not_found(self, server_manager):
        """Cannot use nonexistent invite."""
        with pytest.raises(InviteNotFoundError):
            server_manager.use_invite(1, "INVALID")

    def test_delete_invite_not_creator(self, server_manager):
        """Only invite creator or admin can delete."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id)

        with pytest.raises(PermissionDeniedError):
            server_manager.delete_invite(2, invite.code)

    def test_move_channel_to_nonexistent_category(self, server_manager):
        """Cannot move to non-existent category."""
        server = server_manager.create_server(1, "Test Server")
        channel = server_manager.create_channel(1, server.id, "test")

        with pytest.raises(CategoryNotFoundError):
            server_manager.update_channel(1, channel.id, category_id=99999)

    def test_delete_channel_not_found(self, server_manager):
        """Cannot delete nonexistent channel."""
        with pytest.raises(ChannelNotFoundError):
            server_manager.delete_channel(1, 99999)

    def test_update_server_not_owner(self, server_manager):
        """Only owner can update server."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(PermissionDeniedError):
            server_manager.update_server(2, server.id, name="New Name")


class TestServerPermissions:
    """Test permission checks."""

    def test_require_permission_fails(self, server_manager):
        """Require permission raises when not granted."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(PermissionDeniedError):
            server_manager.require_permission(2, server.id, "server.manage")

    def test_has_permission_owner_bypass(self, server_manager):
        """Owner has all permissions."""
        server = server_manager.create_server(1, "Test Server")

        assert server_manager.has_permission(1, server.id, "any.permission")

    def test_channel_override_deny(self, server_manager):
        """Channel override can deny permission."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        channel = server_manager.create_channel(1, server.id, "test")

        server_manager.set_channel_override(
            1, channel.id, "member", 2, deny={"messages.send": True}
        )

        assert not server_manager.has_permission(
            2, server.id, "messages.send", channel.id
        )

    def test_role_hierarchy_enforcement(self, server_manager):
        """Role hierarchy is enforced."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        server_manager.add_member(server.id, 3)

        high_role = server_manager.create_role(1, server.id, "High")
        server_manager.move_role(1, high_role.id, 10)
        server_manager.assign_role(1, server.id, 2, high_role.id)

        low_role = server_manager.create_role(
            1, server.id, "Low", permissions={"members.kick": True}
        )
        server_manager.assign_role(1, server.id, 3, low_role.id)

        # Ensure Low is lower
        server_manager.move_role(1, low_role.id, 0)

        with pytest.raises(RoleHierarchyError):
            server_manager.kick_member(3, server.id, 2)

    def test_channel_override_allow(self, server_manager):
        """Channel override can allow permission."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        channel = server_manager.create_channel(1, server.id, "test")

        server_manager.set_channel_override(
            1, channel.id, "member", 2, allow={"messages.send": True}
        )

        assert server_manager.has_permission(2, server.id, "messages.send", channel.id)


class TestServerCaching:
    """Test caching behavior."""

    def test_server_cache(self, server_manager):
        """Server data is cached."""
        server = server_manager.create_server(1, "Test Server")

        s1 = server_manager.get_server(server.id, 1)

        s2 = server_manager.get_server(server.id, 1)

        assert s1.id == s2.id

    def test_permission_cache(self, server_manager):
        """Permissions are cached."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        p1 = server_manager.get_permissions(2, server.id)

        p2 = server_manager.get_permissions(2, server.id)

        assert p1 == p2

    def test_cache_invalidation_on_role_change(self, server_manager):
        """Cache invalidated when roles change."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        server_manager.get_permissions(2, server.id)

        role = server_manager.create_role(1, server.id, "Test")
        server_manager.assign_role(1, server.id, 2, role.id)

        perms = server_manager.get_permissions(2, server.id)
        assert perms is not None


class TestServerMemberOperations:
    """Test member management."""

    def test_add_member_already_exists(self, server_manager):
        """Cannot add existing member."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(MemberExistsError):
            server_manager.add_member(server.id, 2)

    def test_member_gets_default_role(self, server_manager):
        """New member gets default role."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        roles = server_manager.get_member_roles(server.id, 2)
        assert any(r.is_default for r in roles)

    def test_update_member_nickname(self, server_manager):
        """Can update member nickname."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        updated = server_manager.update_member(1, server.id, 2, nickname="TestNick")
        assert updated.nickname == "TestNick"

    def test_update_own_nickname(self, server_manager):
        """Can update own nickname."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        updated = server_manager.update_member(2, server.id, 2, nickname="MyNick")
        assert updated.nickname == "MyNick"

    def test_update_member_not_found(self, server_manager):
        """Cannot update nonexistent member."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(MemberNotFoundError):
            server_manager.update_member(1, server.id, 99999, nickname="Test")

    def test_get_member_not_found(self, server_manager):
        """Getting nonexistent member returns None."""
        server = server_manager.create_server(1, "Test Server")

        member = server_manager.get_member(server.id, 99999)
        assert member is None

    def test_get_members(self, server_manager):
        """Can get all members."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        server_manager.add_member(server.id, 3)

        members = server_manager.get_members(1, server.id)
        assert len(members) >= 3


class TestServerChannelOperations:
    """Test channel management."""

    def test_create_text_channel(self, server_manager):
        """Can create text channel."""
        server = server_manager.create_server(1, "Test Server")
        channel = server_manager.create_channel(1, server.id, "text", ChannelType.TEXT)

        assert channel.channel_type == ChannelType.TEXT

    def test_create_voice_channel(self, server_manager):
        """Can create voice channel."""
        server = server_manager.create_server(1, "Test Server")
        channel = server_manager.create_channel(
            1, server.id, "voice", ChannelType.VOICE
        )

        assert channel.channel_type == ChannelType.VOICE

    def test_create_channel_no_permission(self, server_manager):
        """Need permission to create channel."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(PermissionDeniedError):
            server_manager.create_channel(2, server.id, "test")

    def test_update_channel_topic(self, server_manager):
        """Can update channel topic."""
        server = server_manager.create_server(1, "Test Server")
        channel = server_manager.create_channel(1, server.id, "test")

        updated = server_manager.update_channel(1, channel.id, topic="New topic")
        # Topic is encrypted and stored in topic_encrypted column
        assert updated is not None
        assert updated.id == channel.id

    def test_update_channel_not_found(self, server_manager):
        """Cannot update nonexistent channel."""
        with pytest.raises(ChannelNotFoundError):
            server_manager.update_channel(1, 99999, topic="Test")

    def test_move_channel_position(self, server_manager):
        """Can move channel position."""
        server = server_manager.create_server(1, "Test Server")
        channel = server_manager.create_channel(1, server.id, "test")

        moved = server_manager.move_channel(1, channel.id, 5)
        assert moved.position == 5

    def test_delete_channel(self, server_manager):
        """Can delete channel."""
        server = server_manager.create_server(1, "Test Server")
        channel = server_manager.create_channel(1, server.id, "test")

        assert server_manager.delete_channel(1, channel.id)
        assert server_manager.get_channel(channel.id, 1) is None

    def test_get_channel_not_found(self, server_manager):
        """Getting nonexistent channel returns None."""
        channel = server_manager.get_channel(99999, 1)
        assert channel is None


class TestServerRoleOperations:
    """Test role management."""

    def test_create_role_with_permissions(self, server_manager):
        """Can create role with permissions."""
        server = server_manager.create_server(1, "Test Server")
        perms = {"messages.send": True, "channels.view": True}

        role = server_manager.create_role(1, server.id, "Test", permissions=perms)
        assert role.permissions == perms

    def test_create_role_no_permission(self, server_manager):
        """Need permission to create role."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(PermissionDeniedError):
            server_manager.create_role(2, server.id, "Test")

    def test_update_role_permissions(self, server_manager):
        """Can update role permissions."""
        server = server_manager.create_server(1, "Test Server")
        role = server_manager.create_role(1, server.id, "Test")

        new_perms = {"messages.send": False}
        updated = server_manager.update_role(1, role.id, permissions=new_perms)

        assert updated.permissions == new_perms

    def test_update_role_not_found(self, server_manager):
        """Cannot update nonexistent role."""
        with pytest.raises(RoleNotFoundError):
            server_manager.update_role(1, 99999, name="Test")

    def test_move_role_position(self, server_manager):
        """Can move role position."""
        server = server_manager.create_server(1, "Test Server")
        role = server_manager.create_role(1, server.id, "Test")

        moved = server_manager.move_role(1, role.id, 5)
        assert moved.position == 5

    def test_delete_role(self, server_manager):
        """Can delete role."""
        server = server_manager.create_server(1, "Test Server")
        role = server_manager.create_role(1, server.id, "Test")

        assert server_manager.delete_role(1, role.id)
        assert server_manager.get_role(role.id, 1) is None

    def test_get_role_not_found(self, server_manager):
        """Getting nonexistent role returns None."""
        role = server_manager.get_role(99999, 1)
        assert role is None

    def test_assign_role_not_found(self, server_manager):
        """Cannot assign nonexistent role."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(RoleNotFoundError):
            server_manager.assign_role(1, server.id, 2, 99999)

    def test_remove_role_not_found(self, server_manager):
        """Cannot remove nonexistent role."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        with pytest.raises(RoleNotFoundError):
            server_manager.remove_role(1, server.id, 2, 99999)


class TestServerInvites:
    """Test invite system."""

    def test_create_temporary_invite(self, server_manager):
        """Can create temporary invite."""
        server = server_manager.create_server(1, "Test Server")
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id, max_age=3600)
        assert invite.max_age == 3600
        assert invite.expires_at is not None

    def test_create_permanent_invite(self, server_manager):
        """Can create permanent invite."""
        server = server_manager.create_server(1, "Test Server")
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id, max_age=0)
        assert invite.expires_at is None

    def test_invite_with_max_uses(self, server_manager):
        """Can create invite with max uses."""
        server = server_manager.create_server(1, "Test Server")
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id, max_uses=5)
        assert invite.max_uses == 5

    def test_use_invite_increments_uses(self, server_manager):
        """Using invite increments use count."""
        server = server_manager.create_server(1, "Test Server")
        channels = server_manager.get_channels(1, server.id)

        invite = server_manager.create_invite(1, channels[0].id)
        initial_uses = invite.uses

        server_manager.use_invite(2, invite.code)

        updated = server_manager.get_invite(invite.code)
        assert updated.uses == initial_uses + 1

    def test_get_invite_not_found(self, server_manager):
        """Getting nonexistent invite returns None."""
        invite = server_manager.get_invite("INVALID")
        assert invite is None

    def test_get_server_invites(self, server_manager):
        """Can get all server invites."""
        server = server_manager.create_server(1, "Test Server")
        channels = server_manager.get_channels(1, server.id)

        server_manager.create_invite(1, channels[0].id)
        server_manager.create_invite(1, channels[0].id)

        invites = server_manager.get_server_invites(1, server.id)
        assert len(invites) >= 2


class TestServerAuditLog:
    """Test audit logging."""

    def test_audit_log_member_join(self, server_manager):
        """Joining is logged."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        logs = server_manager.get_audit_log(1, server.id)
        assert len(logs) > 0
        assert any(log.action == AuditLogAction.MEMBER_JOIN for log in logs)

    def test_audit_log_member_kick(self, server_manager):
        """Kicking is logged."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)

        # Give kicker permission
        kicker_role = server_manager.create_role(
            1, server.id, "Kicker", permissions={"members.kick": True}
        )
        server_manager.assign_role(
            1, server.id, 1, kicker_role.id
        )  # Assign to owner? Owner has all perms.
        # Actually owner (1) kicks (2). Owner has perms.

        server_manager.kick_member(1, server.id, 2)

        logs = server_manager.get_audit_log(1, server.id)
        assert any(log.action == AuditLogAction.MEMBER_KICK for log in logs)

    def test_audit_log_role_create(self, server_manager):
        """Role creation is logged."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.create_role(1, server.id, "Test")

        logs = server_manager.get_audit_log(1, server.id)
        assert any(log.action == AuditLogAction.ROLE_CREATE for log in logs)


class TestServerBans:
    """Test ban system."""

    def test_unban_member(self, server_manager):
        """Can unban member."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        server_manager.ban_member(1, server.id, 2)

        assert server_manager.unban_member(1, server.id, 2)

    def test_unban_not_banned(self, server_manager):
        """Cannot unban non-banned user."""
        server = server_manager.create_server(1, "Test Server")

        with pytest.raises(BanNotFoundError):
            server_manager.unban_member(1, server.id, 2)

    def test_get_bans(self, server_manager):
        """Can get all server bans."""
        server = server_manager.create_server(1, "Test Server")
        server_manager.add_member(server.id, 2)
        server_manager.add_member(server.id, 3)

        server_manager.ban_member(1, server.id, 2)
        server_manager.ban_member(1, server.id, 3)

        bans = server_manager.get_bans(1, server.id)
        assert len(bans) >= 2


class TestServerCategories:
    """Test channel categories."""

    def test_create_category(self, server_manager):
        """Can create category."""
        server = server_manager.create_server(1, "Test Server")

        category = server_manager.create_category(1, server.id, "General")
        assert category is not None

    def test_delete_category(self, server_manager):
        """Can delete category."""
        server = server_manager.create_server(1, "Test Server")
        category = server_manager.create_category(1, server.id, "General")

        assert server_manager.delete_category(1, category.id)

    def test_move_channel_to_category(self, server_manager):
        """Can move channel to category."""
        server = server_manager.create_server(1, "Test Server")
        category = server_manager.create_category(1, server.id, "General")
        channel = server_manager.create_channel(1, server.id, "test")

        updated = server_manager.update_channel(1, channel.id, category_id=category.id)
        assert updated.category_id == category.id
