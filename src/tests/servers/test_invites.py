"""
Tests for invite operations.
"""

import pytest
import time


class TestCreateInvite:
    """Tests for invite creation."""

    def test_create_invite_success(self, server_with_channels):
        """Test creating an invite."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(
            user_id=owner.id,
            channel_id=general.id
        )
        
        assert invite is not None
        assert invite.code is not None
        assert len(invite.code) == 8
        assert invite.server_id == server.id
        assert invite.channel_id == general.id

    def test_create_invite_with_max_age(self, server_with_channels):
        """Test creating invite with max age."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(
            user_id=owner.id,
            channel_id=general.id,
            max_age=3600  # 1 hour
        )
        
        assert invite.max_age == 3600
        assert invite.expires_at is not None

    def test_create_invite_with_max_uses(self, server_with_channels):
        """Test creating invite with max uses."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(
            user_id=owner.id,
            channel_id=general.id,
            max_uses=10
        )
        
        assert invite.max_uses == 10

    def test_create_invite_never_expires(self, server_with_channels):
        """Test creating invite that never expires."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(
            user_id=owner.id,
            channel_id=general.id,
            max_age=0  # Never expires
        )
        
        assert invite.expires_at is None

    def test_create_invite_temporary(self, server_with_channels):
        """Test creating temporary invite."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(
            user_id=owner.id,
            channel_id=general.id,
            temporary=True
        )
        
        assert invite.temporary is True

    def test_create_invite_without_permission_fails(self, server_with_channels):
        """Test that creating invite without permission fails."""
        server, owner, _, member_user, _, general, _, _, _, servers = server_with_channels
        
        # Remove invite permission from default role but keep channel view
        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]
        
        servers.update_role(
            user_id=owner.id,
            role_id=default_role.id,
            permissions={
                "invites.create": False,
                "channels.view": True,
                "messages.send": True,
                "messages.read": True
            }
        )
        
        with pytest.raises(servers.PermissionDeniedError):
            servers.create_invite(
                user_id=member_user.id,
                channel_id=general.id
            )


class TestGetInvite:
    """Tests for getting invite info."""

    def test_get_invite_success(self, server_with_channels):
        """Test getting an invite."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        created = servers.create_invite(owner.id, general.id)
        
        invite = servers.get_invite(created.code)
        
        assert invite is not None
        assert invite.code == created.code

    def test_get_invite_nonexistent(self, users):
        """Test getting nonexistent invite."""
        _, _, _, _, servers = users
        
        invite = servers.get_invite("NONEXISTENT")
        
        assert invite is None

    def test_get_invite_revoked(self, server_with_channels):
        """Test getting revoked invite returns None."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(owner.id, general.id)
        servers.delete_invite(owner.id, invite.code)
        
        result = servers.get_invite(invite.code)
        
        assert result is None


class TestGetInvites:
    """Tests for listing invites."""

    def test_get_invites_returns_server_invites(self, server_with_channels):
        """Test getting all invites for a server."""
        server, owner, _, _, _, general, announcements, _, _, servers = server_with_channels
        
        invite1 = servers.create_invite(owner.id, general.id)
        invite2 = servers.create_invite(owner.id, announcements.id)
        
        invites = servers.get_invites(owner.id, server.id)
        
        codes = [i.code for i in invites]
        assert invite1.code in codes
        assert invite2.code in codes

    def test_get_invites_without_permission_fails(self, server_with_channels):
        """Test that getting invites without permission fails."""
        server, _, _, member_user, _, _, _, _, _, servers = server_with_channels
        
        with pytest.raises(servers.PermissionDeniedError):
            servers.get_invites(member_user.id, server.id)


class TestUseInvite:
    """Tests for using invites."""

    def test_use_invite_success(self, server_with_channels, base_users):
        """Test using an invite to join."""
        server, owner, _, _, outsider, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(owner.id, general.id)
        
        member = servers.use_invite(outsider.id, invite.code)
        
        assert member is not None
        assert member.user_id == outsider.id
        assert member.server_id == server.id

    def test_use_invite_increments_uses(self, server_with_channels, base_users):
        """Test that using invite increments uses."""
        server, owner, _, _, outsider, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(owner.id, general.id)
        assert invite.uses == 0
        
        servers.use_invite(outsider.id, invite.code)
        
        updated = servers.get_invite(invite.code)
        assert updated.uses == 1

    def test_use_invite_nonexistent_fails(self, users):
        """Test using nonexistent invite fails."""
        owner, _, _, _, servers = users
        
        with pytest.raises(servers.InviteNotFoundError):
            servers.use_invite(owner.id, "NONEXISTENT")

    def test_use_invite_max_uses_reached_fails(self, server_with_channels, base_users):
        """Test using invite at max uses fails."""
        server, owner, _, _, outsider, general, _, _, _, servers = server_with_channels
        _, _, _, new_user, auth, _, _ = base_users
        
        invite = servers.create_invite(owner.id, general.id, max_uses=1)
        
        # Use once
        servers.use_invite(outsider.id, invite.code)
        
        # Create another user to try
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        another_user = auth.register(
            username=f"another_{unique_id}",
            email=f"another_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        # Second use should fail
        with pytest.raises(servers.InviteMaxUsesError):
            servers.use_invite(another_user.id, invite.code)

    def test_use_invite_already_member(self, server_with_channels):
        """Test using invite when already member."""
        server, owner, _, member_user, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(owner.id, general.id)
        
        with pytest.raises(servers.MemberExistsError):
            servers.use_invite(member_user.id, invite.code)


class TestDeleteInvite:
    """Tests for deleting invites."""

    def test_delete_own_invite(self, server_with_channels):
        """Test deleting own invite."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels
        
        invite = servers.create_invite(owner.id, general.id)
        
        result = servers.delete_invite(owner.id, invite.code)
        
        assert result is True
        assert servers.get_invite(invite.code) is None

    def test_delete_others_invite_with_permission(self, server_with_channels):
        """Test deleting others' invite with manage permission."""
        server, owner, admin_user, _, _, general, _, _, _, servers = server_with_channels
        
        # Give admin invites.manage permission
        roles = servers.get_roles(owner.id, server.id)
        admin_roles = servers.get_member_roles(server.id, admin_user.id)
        admin_role = [r for r in admin_roles if not r.is_default][0]
        
        servers.update_role(
            user_id=owner.id,
            role_id=admin_role.id,
            permissions={**admin_role.permissions, "invites.manage": True}
        )
        
        invite = servers.create_invite(owner.id, general.id)
        
        result = servers.delete_invite(admin_user.id, invite.code)
        
        assert result is True

    def test_delete_nonexistent_invite_fails(self, users):
        """Test deleting nonexistent invite fails."""
        owner, _, _, _, servers = users
        
        with pytest.raises(servers.InviteNotFoundError):
            servers.delete_invite(owner.id, "NONEXISTENT")
