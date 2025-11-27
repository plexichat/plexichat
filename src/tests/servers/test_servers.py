"""
Tests for server CRUD operations.
"""

import pytest


class TestCreateServer:
    """Tests for server creation."""

    def test_create_server_success(self, users):
        """Test creating a server."""
        owner, _, _, _, servers = users
        
        server = servers.create_server(
            owner_id=owner.id,
            name="My Server",
            description="A test server"
        )
        
        assert server is not None
        assert server.name == "My Server"
        assert server.owner_id == owner.id
        assert server.description == "A test server"

    def test_create_server_creates_default_role(self, users):
        """Test that creating a server creates @everyone role."""
        owner, _, _, _, servers = users
        
        server = servers.create_server(owner_id=owner.id, name="Role Test Server")
        
        roles = servers.get_roles(owner.id, server.id)
        default_roles = [r for r in roles if r.is_default]
        
        assert len(default_roles) == 1
        assert default_roles[0].name == "@everyone"

    def test_create_server_creates_general_channel(self, users):
        """Test that creating a server creates general channel."""
        owner, _, _, _, servers = users
        
        server = servers.create_server(owner_id=owner.id, name="Channel Test Server")
        
        channels = servers.get_channels(owner.id, server.id)
        
        assert len(channels) >= 1
        assert any(c.name == "general" for c in channels)

    def test_create_server_owner_is_member(self, users):
        """Test that owner is automatically a member."""
        owner, _, _, _, servers = users
        
        server = servers.create_server(owner_id=owner.id, name="Member Test Server")
        
        member = servers.get_member(server.id, owner.id)
        
        assert member is not None
        assert member.user_id == owner.id

    def test_create_server_empty_name_fails(self, users):
        """Test that empty name fails."""
        owner, _, _, _, servers = users
        
        with pytest.raises(servers.InvalidServerNameError):
            servers.create_server(owner_id=owner.id, name="")

    def test_create_server_whitespace_name_fails(self, users):
        """Test that whitespace-only name fails."""
        owner, _, _, _, servers = users
        
        with pytest.raises(servers.InvalidServerNameError):
            servers.create_server(owner_id=owner.id, name="   ")

    def test_create_server_long_name_fails(self, users):
        """Test that overly long name fails."""
        owner, _, _, _, servers = users
        
        with pytest.raises(servers.InvalidServerNameError):
            servers.create_server(owner_id=owner.id, name="x" * 200)


class TestGetServer:
    """Tests for getting server info."""

    def test_get_server_as_member(self, server_with_members):
        """Test getting server as a member."""
        server, owner, admin_user, member_user, _, _, servers = server_with_members
        
        result = servers.get_server(server.id, member_user.id)
        
        assert result is not None
        assert result.id == server.id
        assert result.name == server.name

    def test_get_server_as_non_member(self, server_with_members):
        """Test getting server as non-member returns None."""
        server, _, _, _, outsider, _, servers = server_with_members
        
        result = servers.get_server(server.id, outsider.id)
        
        assert result is None

    def test_get_server_nonexistent(self, users):
        """Test getting nonexistent server."""
        owner, _, _, _, servers = users
        
        result = servers.get_server(999999999, owner.id)
        
        assert result is None

    def test_get_server_includes_counts(self, server_with_members):
        """Test that server includes member/channel/role counts."""
        server, owner, _, _, _, _, servers = server_with_members
        
        result = servers.get_server(server.id, owner.id)
        
        assert result.member_count >= 3  # owner, admin, member
        assert result.channel_count >= 1
        assert result.role_count >= 1


class TestGetServers:
    """Tests for listing servers."""

    def test_get_servers_returns_user_servers(self, server_with_members):
        """Test getting all servers user is in."""
        server, owner, _, _, _, _, servers = server_with_members
        
        result = servers.get_servers(owner.id)
        
        assert len(result) >= 1
        assert any(s.id == server.id for s in result)

    def test_get_servers_excludes_non_member(self, server_with_members):
        """Test that non-member doesn't see server."""
        server, _, _, _, outsider, _, servers = server_with_members
        
        result = servers.get_servers(outsider.id)
        
        assert not any(s.id == server.id for s in result)


class TestUpdateServer:
    """Tests for updating server settings."""

    def test_update_server_name(self, fresh_server):
        """Test updating server name."""
        server, owner, servers = fresh_server
        
        updated = servers.update_server(
            user_id=owner.id,
            server_id=server.id,
            name="Updated Name"
        )
        
        assert updated.name == "Updated Name"

    def test_update_server_description(self, fresh_server):
        """Test updating server description."""
        server, owner, servers = fresh_server
        
        updated = servers.update_server(
            user_id=owner.id,
            server_id=server.id,
            description="New description"
        )
        
        assert updated.description == "New description"

    def test_update_server_by_non_admin_fails(self, server_with_members):
        """Test that non-admin cannot update server."""
        server, _, _, member_user, _, _, servers = server_with_members
        
        with pytest.raises(servers.PermissionDeniedError):
            servers.update_server(
                user_id=member_user.id,
                server_id=server.id,
                name="Hacked Name"
            )

    def test_update_server_by_admin_succeeds(self, server_with_members):
        """Test that admin can update server."""
        server, owner, admin_user, _, _, admin_role, servers = server_with_members
        
        # Give admin server.manage permission
        servers.update_role(
            user_id=owner.id,
            role_id=admin_role.id,
            permissions={"server.manage": True}
        )
        
        updated = servers.update_server(
            user_id=admin_user.id,
            server_id=server.id,
            description="Admin updated"
        )
        
        assert updated.description == "Admin updated"


class TestDeleteServer:
    """Tests for deleting servers."""

    def test_delete_server_by_owner(self, fresh_server):
        """Test owner can delete server."""
        server, owner, servers = fresh_server
        
        result = servers.delete_server(owner.id, server.id)
        
        assert result is True
        assert servers.get_server(server.id, owner.id) is None

    def test_delete_server_by_non_owner_fails(self, server_with_members):
        """Test non-owner cannot delete server."""
        server, _, admin_user, _, _, _, servers = server_with_members
        
        with pytest.raises(servers.ServerAccessDeniedError):
            servers.delete_server(admin_user.id, server.id)


class TestTransferOwnership:
    """Tests for transferring server ownership."""

    def test_transfer_ownership_success(self, server_with_members):
        """Test transferring ownership."""
        server, owner, admin_user, _, _, _, servers = server_with_members
        
        updated = servers.transfer_ownership(owner.id, server.id, admin_user.id)
        
        assert updated.owner_id == admin_user.id

    def test_transfer_ownership_by_non_owner_fails(self, server_with_members):
        """Test non-owner cannot transfer ownership."""
        server, _, admin_user, member_user, _, _, servers = server_with_members
        
        with pytest.raises(servers.ServerAccessDeniedError):
            servers.transfer_ownership(admin_user.id, server.id, member_user.id)

    def test_transfer_ownership_to_non_member_fails(self, server_with_members):
        """Test cannot transfer to non-member."""
        server, owner, _, _, outsider, _, servers = server_with_members
        
        with pytest.raises(servers.MemberNotFoundError):
            servers.transfer_ownership(owner.id, server.id, outsider.id)
