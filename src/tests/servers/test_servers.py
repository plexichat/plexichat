"""
Tests for server CRUD operations.
"""

import pytest


import pytest
import asyncio
import uuid

@pytest.mark.asyncio
class TestServersAsync:
    """Enhanced asynchronous server tests."""

    async def test_create_server_success(self, user_pool, modules):
        """Test creating a server and verify initial state."""
        owner = user_pool.get_user()
        servers = modules.servers
        
        server = await asyncio.to_thread(
            servers.create_server,
            owner_id=owner.id,
            name="Async Server",
            description="Integration test"
        )
        
        assert server.name == "Async Server"
        assert server.owner_id == owner.id
        
        # Verify initial resources
        channels = await asyncio.to_thread(servers.get_channels, owner.id, server.id)
        assert any(c.name == "general" for c in channels)
        
        roles = await asyncio.to_thread(servers.get_roles, owner.id, server.id)
        assert any(r.name == "@everyone" for r in roles)

    async def test_concurrent_server_creation(self, user_pool, modules):
        """Test multiple users creating servers concurrently to verify ID generation and database safety."""
        users = [user_pool.get_user() for _ in range(10)]
        servers = modules.servers
        
        tasks = [
            asyncio.to_thread(servers.create_server, u.id, f"Server {i}")
            for i, u in enumerate(users)
        ]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 10
        # IDs should be unique
        assert len({s.id for s in results}) == 10

    async def test_permission_propagation(self, user_pool, modules):
        """Test that updating role permissions correctly propagates to members."""
        owner = user_pool.get_user()
        member = user_pool.get_user()
        servers = modules.servers
        
        server = await asyncio.to_thread(servers.create_server, owner.id, "Perm Server")
        await asyncio.to_thread(servers.add_member, server.id, member.id)
        
        # Create a moderator role
        mod_role = await asyncio.to_thread(
            servers.create_role, owner.id, server.id, "Moderator", 
            permissions={"server.manage": True}
        )
        
        # Member should not have permission yet
        assert not await asyncio.to_thread(servers.has_permission, member.id, server.id, "server.manage")
        
        # Assign role
        await asyncio.to_thread(servers.assign_role, owner.id, server.id, member.id, mod_role.id)
        
        # Member should now have permission (testing cache invalidation)
        assert await asyncio.to_thread(servers.has_permission, member.id, server.id, "server.manage")

    async def test_ownership_transfer_integrity(self, user_pool, modules):
        """Test server ownership transfer and verify old/new owner permissions."""
        owner = user_pool.get_user()
        new_owner = user_pool.get_user()
        servers = modules.servers
        
        server = await asyncio.to_thread(servers.create_server, owner.id, "Transfer Server")
        await asyncio.to_thread(servers.add_member, server.id, new_owner.id)
        
        # Transfer
        await asyncio.to_thread(servers.transfer_ownership, owner.id, server.id, new_owner.id)
        
        # Verify
        updated_server = await asyncio.to_thread(servers.get_server, server.id, new_owner.id)
        assert updated_server.owner_id == new_owner.id
        
        # Old owner should still be a member but not owner
        member_record = await asyncio.to_thread(servers.get_member, server.id, owner.id)
        assert member_record is not None
        assert not await asyncio.to_thread(servers.has_permission, owner.id, server.id, "administrator") # Only owner has implicit admin

    async def test_bulk_member_management(self, user_pool, modules):
        """Test adding and removing many members in parallel."""
        owner = user_pool.get_user()
        members = [user_pool.get_user() for _ in range(20)]
        servers = modules.servers
        
        server = await asyncio.to_thread(servers.create_server, owner.id, "Raid Test Server")
        
        # Add members in parallel
        await asyncio.gather(*[
            asyncio.to_thread(servers.add_member, server.id, m.id) for m in members
        ])
        
        # Verify count
        fetched_server = await asyncio.to_thread(servers.get_server, server.id, owner.id)
        assert fetched_server.member_count == 21 # members + owner

