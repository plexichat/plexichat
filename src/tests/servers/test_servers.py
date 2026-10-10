"""
Tests for server CRUD operations.
"""

import pytest
from unittest.mock import patch

import asyncio


@pytest.mark.asyncio
class TestServersAsync:
    """Enhanced asynchronous server tests."""

    async def test_create_server_success(self, server_manager, test_user):
        """Test creating a server and verify initial state."""
        server = await asyncio.to_thread(
            server_manager.create_server,
            owner_id=test_user.id,
            name="Async Server",
            description="Integration test",
        )

        assert server.name == "Async Server"
        assert server.owner_id == test_user.id

        # Verify initial resources
        channels = await asyncio.to_thread(
            server_manager.get_channels, test_user.id, server.id
        )
        assert any(c.name == "general" for c in channels)

        roles = await asyncio.to_thread(
            server_manager.get_roles, test_user.id, server.id
        )
        assert any(r.name == "@everyone" for r in roles)

    async def test_concurrent_server_creation(self, server_manager, auth_manager):
        """Test multiple users creating servers concurrently to verify ID generation and database safety."""
        from src.utils import encryption
        import uuid

        users = []
        for _ in range(10):
            with patch.object(
                encryption, "hash_password", return_value="fake_hash_$test"
            ):
                user = auth_manager.register(
                    username=f"testuser_{uuid.uuid4().hex[:8]}",
                    email=f"test_{uuid.uuid4().hex[:8]}@example.com",
                    password="TestPass123!",
                )
            users.append(user)

        tasks = [
            asyncio.to_thread(server_manager.create_server, u.id, f"Server {i}")
            for i, u in enumerate(users)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        # IDs should be unique
        assert len({s.id for s in results}) == 10

    async def test_permission_propagation(self, server_manager, two_users):
        """Test that updating role permissions correctly propagates to members."""
        owner, member = two_users

        server = await asyncio.to_thread(
            server_manager.create_server, owner.id, "Perm Server"
        )
        await asyncio.to_thread(server_manager.add_member, server.id, member.id)

        # Create a moderator role
        mod_role = await asyncio.to_thread(
            server_manager.create_role,
            owner.id,
            server.id,
            "Moderator",
            permissions={"server.manage": True},
        )

        # Member should not have permission yet
        assert not await asyncio.to_thread(
            server_manager.has_permission, member.id, server.id, "server.manage"
        )

        # Assign role
        await asyncio.to_thread(
            server_manager.assign_role, owner.id, server.id, member.id, mod_role.id
        )

        # Member should now have permission (testing cache invalidation)
        assert await asyncio.to_thread(
            server_manager.has_permission, member.id, server.id, "server.manage"
        )

    async def test_ownership_transfer_integrity(self, server_manager, two_users):
        """Test server ownership transfer and verify old/new owner permissions."""
        owner, new_owner = two_users

        server = await asyncio.to_thread(
            server_manager.create_server, owner.id, "Transfer Server"
        )
        await asyncio.to_thread(server_manager.add_member, server.id, new_owner.id)

        # Transfer
        await asyncio.to_thread(
            server_manager.transfer_ownership, owner.id, server.id, new_owner.id
        )

        # Verify
        updated_server = await asyncio.to_thread(
            server_manager.get_server, server.id, new_owner.id
        )
        assert updated_server.owner_id == new_owner.id

        # Old owner should still be a member but not owner
        member_record = await asyncio.to_thread(
            server_manager.get_member, server.id, owner.id
        )
        assert member_record is not None
        assert not await asyncio.to_thread(
            server_manager.has_permission, owner.id, server.id, "administrator"
        )  # Only owner has implicit admin

    async def test_bulk_member_management(self, server_manager, auth_manager):
        """Test adding and removing many members in parallel."""
        from src.utils import encryption
        import uuid

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username=f"testuser_{uuid.uuid4().hex[:8]}",
                email=f"test_{uuid.uuid4().hex[:8]}@example.com",
                password="TestPass123!",
            )

        members = []
        for _ in range(10):
            with patch.object(
                encryption, "hash_password", return_value="fake_hash_$test"
            ):
                member = auth_manager.register(
                    username=f"testuser_{uuid.uuid4().hex[:8]}",
                    email=f"test_{uuid.uuid4().hex[:8]}@example.com",
                    password="TestPass123!",
                )
            members.append(member)

        server = await asyncio.to_thread(
            server_manager.create_server, owner.id, "Raid Test Server"
        )

        # Add members in parallel
        await asyncio.gather(
            *[
                asyncio.to_thread(server_manager.add_member, server.id, m.id)
                for m in members
            ]
        )

        # Verify count
        fetched_server = await asyncio.to_thread(
            server_manager.get_server, server.id, owner.id
        )
        assert fetched_server.member_count == 11  # members + owner


@pytest.mark.asyncio
class TestUpdateServerSettings:
    """Overview settings and default channel must actually persist."""

    async def _create(self, server_manager, owner, name):
        return await asyncio.to_thread(server_manager.create_server, owner.id, name)

    async def test_default_channel_id_persists(self, server_manager, two_users):
        """H5 regression: default_channel_id must be written, not just validated."""
        owner, _ = two_users
        server = await self._create(server_manager, owner, "Default Chan Server")

        channels = await asyncio.to_thread(
            server_manager.get_channels, owner.id, server.id
        )
        general = [c for c in channels if c.name == "general"][0]

        await asyncio.to_thread(
            server_manager.update_server,
            owner.id,
            server.id,
            default_channel_id=general.id,
        )

        updated = await asyncio.to_thread(
            server_manager.get_server, server.id, owner.id
        )
        assert updated.default_channel_id == general.id

    async def test_overview_settings_persist(self, server_manager, two_users):
        """M6 regression: verification/notification/reaction caps persist."""
        owner, _ = two_users
        server = await self._create(server_manager, owner, "Overview Server")

        await asyncio.to_thread(
            server_manager.update_server,
            owner.id,
            server.id,
            verification_level=3,
            default_message_notifications=1,
            max_reactions_per_message=50,
        )

        updated = await asyncio.to_thread(
            server_manager.get_server, server.id, owner.id
        )
        assert updated.verification_level == 3
        assert updated.default_message_notifications == 1
        assert updated.max_reactions_per_message == 50

    async def test_overview_settings_range_validation(self, server_manager, two_users):
        """Out-of-range internal values are rejected."""
        owner, _ = two_users
        server = await self._create(server_manager, owner, "Range Server")

        import pytest

        with pytest.raises(ValueError):
            await asyncio.to_thread(
                server_manager.update_server,
                owner.id,
                server.id,
                verification_level=9,
            )
        with pytest.raises(ValueError):
            await asyncio.to_thread(
                server_manager.update_server,
                owner.id,
                server.id,
                max_reactions_per_message=500,
            )
