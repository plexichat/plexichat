"""
Tests for member operations.
"""

import uuid

import pytest


class TestAddMember:
    """Tests for adding members."""

    def test_add_member_success(self, fresh_server, base_users):
        """Test adding a member."""
        server, owner, servers = fresh_server
        _, _, _, outsider, _, _, _ = base_users

        member = servers.add_member(server.id, outsider.id)

        assert member is not None
        assert member.user_id == outsider.id
        assert member.server_id == server.id

    def test_add_member_gets_default_role(self, fresh_server, base_users):
        """Test that new member gets @everyone role."""
        server, owner, servers = fresh_server
        _, _, _, outsider, _, _, _ = base_users

        servers.add_member(server.id, outsider.id)

        roles = servers.get_member_roles(server.id, outsider.id)
        assert any(r.is_default for r in roles)

    def test_add_existing_member_fails(self, server_with_members):
        """Test that adding existing member fails."""
        server, _, admin_user, _, _, _, servers = server_with_members

        with pytest.raises(servers.MemberExistsError):
            servers.add_member(server.id, admin_user.id)

    def test_add_banned_user_fails(self, server_with_members, base_users):
        """Test that adding banned user fails."""
        server, owner, _, _, outsider, _, servers = server_with_members

        # Ban the outsider first
        servers.ban_member(owner.id, server.id, outsider.id, reason="Test ban")

        # Try to add them
        with pytest.raises(servers.UserBannedError):
            servers.add_member(server.id, outsider.id)


class TestGetMember:
    """Tests for getting member info."""

    def test_get_member_success(self, server_with_members):
        """Test getting a member."""
        server, _, admin_user, _, _, _, servers = server_with_members

        member = servers.get_member(server.id, admin_user.id)

        assert member is not None
        assert member.user_id == admin_user.id

    def test_get_member_nonexistent(self, server_with_members):
        """Test getting nonexistent member."""
        server, _, _, _, outsider, _, servers = server_with_members

        member = servers.get_member(server.id, outsider.id)

        assert member is None

    def test_get_member_includes_roles(self, server_with_members):
        """Test that member includes role IDs."""
        server, _, admin_user, _, _, admin_role, servers = server_with_members

        member = servers.get_member(server.id, admin_user.id)

        assert admin_role.id in member.roles


class TestGetMembers:
    """Tests for listing members."""

    def test_get_members_returns_server_members(self, server_with_members):
        """Test getting all members."""
        server, owner, admin_user, member_user, _, _, servers = server_with_members

        members = servers.get_members(owner.id, server.id)

        user_ids = [m.user_id for m in members]
        assert owner.id in user_ids
        assert admin_user.id in user_ids
        assert member_user.id in user_ids

    def test_get_members_respects_limit(self, server_with_members):
        """Test that limit is respected."""
        server, owner, _, _, _, _, servers = server_with_members

        members = servers.get_members(owner.id, server.id, limit=1)

        assert len(members) == 1

    def test_get_members_as_non_member_fails(self, server_with_members):
        """Test that non-member cannot list members."""
        server, _, _, _, outsider, _, servers = server_with_members

        with pytest.raises(servers.ServerAccessDeniedError):
            servers.get_members(outsider.id, server.id)


class TestUpdateMember:
    """Tests for updating members."""

    def test_update_own_nickname(self, server_with_members):
        """Test updating own nickname."""
        server, _, _, member_user, _, _, servers = server_with_members

        updated = servers.update_member(
            user_id=member_user.id,
            server_id=server.id,
            member_user_id=member_user.id,
            nickname="Cool Nick",
        )

        assert updated.nickname == "Cool Nick"

    def test_update_others_nickname_with_permission(self, server_with_members):
        """Test updating others' nickname with permission."""
        server, owner, _, member_user, _, _, servers = server_with_members

        updated = servers.update_member(
            user_id=owner.id,
            server_id=server.id,
            member_user_id=member_user.id,
            nickname="Assigned Nick",
        )

        assert updated.nickname == "Assigned Nick"

    def test_update_others_nickname_without_permission_fails(self, server_with_members):
        """Test that updating others' nickname without permission fails."""
        server, _, _, member_user, _, _, servers = server_with_members
        auth = servers._auth
        unique_id = uuid.uuid4().hex[:8]
        other_user = auth.register(
            f"nickname_target_{unique_id}",
            f"nickname_target_{unique_id}@example.com",
            "TestPass123!",
        )
        servers.add_member(server.id, other_user.id)

        with pytest.raises(servers.PermissionDeniedError):
            servers.update_member(
                user_id=member_user.id,
                server_id=server.id,
                member_user_id=other_user.id,
                nickname="Not Allowed",
            )


class TestRemoveMember:
    """Tests for leaving servers."""

    def test_leave_server(self, server_with_members):
        """Test leaving a server."""
        server, _, _, member_user, _, _, servers = server_with_members

        result = servers.remove_member(member_user.id, server.id)

        assert result is True
        assert servers.get_member(server.id, member_user.id) is None

    def test_owner_cannot_leave(self, fresh_server):
        """Test that owner cannot leave."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.OwnerCannotLeaveError):
            servers.remove_member(owner.id, server.id)


class TestKickMember:
    """Tests for kicking members."""

    def test_kick_member_success(self, server_with_members):
        """Test kicking a member."""
        server, owner, _, member_user, _, _, servers = server_with_members

        result = servers.kick_member(owner.id, server.id, member_user.id)

        assert result is True
        assert servers.get_member(server.id, member_user.id) is None

    def test_kick_member_with_reason(self, server_with_members):
        """Test kicking with reason."""
        server, owner, _, member_user, _, _, servers = server_with_members

        result = servers.kick_member(
            owner.id, server.id, member_user.id, reason="Rule violation"
        )

        assert result is True

    def test_kick_owner_fails(self, server_with_members):
        """Test that kicking owner fails."""
        server, owner, admin_user, _, _, _, servers = server_with_members

        with pytest.raises(servers.CannotModifyOwnerError):
            servers.kick_member(admin_user.id, server.id, owner.id)

    def test_kick_without_permission_fails(self, server_with_members):
        """Test that kicking without permission fails."""
        server, _, _, member_user, _, _, servers = server_with_members
        auth = servers._auth
        unique_id = uuid.uuid4().hex[:8]
        other_user = auth.register(
            f"kick_target_{unique_id}",
            f"kick_target_{unique_id}@example.com",
            "TestPass123!",
        )
        servers.add_member(server.id, other_user.id)

        with pytest.raises(servers.PermissionDeniedError):
            servers.kick_member(member_user.id, server.id, other_user.id)

    def test_kick_higher_role_fails(self, server_with_members):
        """Test that kicking higher role fails."""
        server, owner, admin_user, member_user, _, admin_role, servers = (
            server_with_members
        )

        # Create a lower role for member with kick permission
        lower_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Lower",
            permissions={"members.kick": True},
        )
        # Move it below admin role
        servers.move_role(owner.id, lower_role.id, position=admin_role.position - 1)
        servers.assign_role(owner.id, server.id, member_user.id, lower_role.id)

        # Member should not be able to kick admin (who has higher role)
        with pytest.raises(servers.RoleHierarchyError):
            servers.kick_member(member_user.id, server.id, admin_user.id)


class TestBanMember:
    """Tests for banning members."""

    def test_ban_member_success(self, server_with_members):
        """Test banning a member."""
        server, owner, _, member_user, _, _, servers = server_with_members

        ban = servers.ban_member(owner.id, server.id, member_user.id)

        assert ban is not None
        assert ban.user_id == member_user.id
        assert servers.get_member(server.id, member_user.id) is None

    def test_ban_member_with_reason(self, server_with_members):
        """Test banning with reason."""
        server, owner, _, member_user, _, _, servers = server_with_members

        ban = servers.ban_member(
            owner.id, server.id, member_user.id, reason="Repeated violations"
        )

        assert ban.reason == "Repeated violations"

    def test_ban_owner_fails(self, server_with_members):
        """Test that banning owner fails."""
        server, owner, admin_user, _, _, _, servers = server_with_members

        with pytest.raises(servers.CannotModifyOwnerError):
            servers.ban_member(admin_user.id, server.id, owner.id)

    def test_ban_already_banned_fails(self, server_with_members):
        """Test that banning already banned user fails."""
        server, owner, _, member_user, _, _, servers = server_with_members

        servers.ban_member(owner.id, server.id, member_user.id)

        with pytest.raises(servers.BanExistsError):
            servers.ban_member(owner.id, server.id, member_user.id)


class TestUnbanMember:
    """Tests for unbanning members."""

    def test_unban_member_success(self, server_with_members):
        """Test unbanning a member."""
        server, owner, _, member_user, _, _, servers = server_with_members

        servers.ban_member(owner.id, server.id, member_user.id)
        result = servers.unban_member(owner.id, server.id, member_user.id)

        assert result is True

        # Should be able to rejoin
        member = servers.add_member(server.id, member_user.id)
        assert member is not None


class TestGetBans:
    """Tests for listing bans."""

    def test_get_bans_returns_server_bans(self, server_with_members):
        """Test getting all bans."""
        server, owner, _, member_user, _, _, servers = server_with_members

        servers.ban_member(owner.id, server.id, member_user.id, reason="Test")

        bans = servers.get_bans(owner.id, server.id)

        assert len(bans) >= 1
        assert any(b.user_id == member_user.id for b in bans)

    def test_get_bans_without_permission_fails(self, server_with_members):
        """Test that getting bans without permission fails."""
        server, owner, admin_user, member_user, _, _, servers = server_with_members

        servers.ban_member(owner.id, server.id, admin_user.id, reason="Test")

        with pytest.raises(servers.PermissionDeniedError):
            servers.get_bans(member_user.id, server.id)


class TestRoleAssignment:
    """Tests for role assignment."""

    def test_assign_role_success(self, server_with_members):
        """Test assigning a role."""
        server, owner, _, member_user, _, admin_role, servers = server_with_members

        result = servers.assign_role(owner.id, server.id, member_user.id, admin_role.id)

        assert result is True

        roles = servers.get_member_roles(server.id, member_user.id)
        assert any(r.id == admin_role.id for r in roles)

    def test_assign_role_already_has(self, server_with_members):
        """Test assigning role member already has."""
        server, owner, admin_user, _, _, admin_role, servers = server_with_members

        # Admin already has the role
        result = servers.assign_role(owner.id, server.id, admin_user.id, admin_role.id)

        assert result is True  # Should succeed silently

    def test_assign_higher_role_fails(self, server_with_members):
        """Test that assigning higher role fails."""
        server, owner, admin_user, member_user, _, admin_role, servers = (
            server_with_members
        )

        # Create a role above admin
        higher_role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Higher"
        )
        servers.move_role(owner.id, higher_role.id, position=admin_role.position + 1)

        # Admin should not be able to assign higher role
        with pytest.raises(servers.RoleHierarchyError):
            servers.assign_role(
                admin_user.id, server.id, member_user.id, higher_role.id
            )


class TestRemoveRole:
    """Tests for role removal."""

    def test_remove_role_success(self, server_with_members):
        """Test removing a role."""
        server, owner, admin_user, _, _, admin_role, servers = server_with_members

        result = servers.remove_role(owner.id, server.id, admin_user.id, admin_role.id)

        assert result is True

        roles = servers.get_member_roles(server.id, admin_user.id)
        assert not any(r.id == admin_role.id for r in roles)

    def test_remove_default_role_fails(self, server_with_members):
        """Test that removing @everyone fails."""
        server, owner, admin_user, _, _, _, servers = server_with_members

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        with pytest.raises(servers.DefaultRoleError):
            servers.remove_role(owner.id, server.id, admin_user.id, default_role.id)


class TestGetMemberRoles:
    """Tests for getting member roles."""

    def test_get_member_roles_success(self, server_with_members):
        """Test getting member roles."""
        server, _, admin_user, _, _, admin_role, servers = server_with_members

        roles = servers.get_member_roles(server.id, admin_user.id)

        assert len(roles) >= 2  # @everyone + Admin
        assert any(r.id == admin_role.id for r in roles)

    def test_get_member_roles_nonexistent_member(self, server_with_members):
        """Test getting roles for nonexistent member."""
        server, _, _, _, outsider, _, servers = server_with_members

        roles = servers.get_member_roles(server.id, outsider.id)

        assert roles == []
