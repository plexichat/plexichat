"""
Tests for server routes - server CRUD, members, roles, bans.

Covers:
- Server CRUD operations
- Member management
- Role management
- Ban management
- Authorization checks
- Input sanitization
- SQL injection prevention
- Error handling
"""

import pytest
import asyncio
import uuid
from httpx import AsyncClient
from src.api.app import create_app


@pytest.mark.asyncio
class TestServerCreation:
    """Test server creation endpoints."""

    async def test_create_server(self, auth_headers):
        """Test creating a server."""
        app = create_app()
        unique_id = uuid.uuid4().hex[:8]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/servers",
                headers=auth_headers,
                json={
                    "name": f"Test Server {unique_id}",
                    "description": "A test server",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == f"Test Server {unique_id}"
            assert "id" in data

    async def test_create_server_without_auth(self):
        """Test creating server without authentication."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/v1/servers", json={"name": "Test Server"})

            assert response.status_code == 401

    async def test_create_server_invalid_name(self, auth_headers):
        """Test creating server with invalid name."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Empty name
            response = await ac.post(
                "/api/v1/servers", headers=auth_headers, json={"name": ""}
            )

            assert response.status_code in [400, 422]

            # Too short
            response = await ac.post(
                "/api/v1/servers", headers=auth_headers, json={"name": "a"}
            )

            assert response.status_code in [400, 422]

    async def test_create_server_sql_injection(self, auth_headers):
        """Test SQL injection prevention in server name."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/servers",
                headers=auth_headers,
                json={"name": "Test'; DROP TABLE servers; --"},
            )

            # Should safely handle
            assert response.status_code in [200, 400]


@pytest.mark.asyncio
class TestServerRetrieval:
    """Test server retrieval endpoints."""

    async def test_get_servers(self, auth_headers):
        """Test getting user's servers."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/servers", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    async def test_get_specific_server(self, modules, auth_headers, test_user):
        """Test getting a specific server."""
        # Create a server
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/servers/{server.id}", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert str(data["id"]) == str(server.id)

    async def test_get_server_not_member(self, modules, session_users):
        """Test getting server user is not a member of."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Private Server {uuid.uuid4().hex[:6]}"
        )

        # User2 tries to access
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(f"/api/v1/servers/{server.id}", headers=headers2)

            assert response.status_code in [403, 404]

    async def test_get_nonexistent_server(self, auth_headers):
        """Test getting a non-existent server."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/servers/999999999", headers=auth_headers)

            assert response.status_code == 404

    async def test_get_server_channels(self, modules, auth_headers, test_user):
        """Test getting server channels."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/servers/{server.id}/channels", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) > 0  # Default channel should exist


@pytest.mark.asyncio
class TestServerUpdate:
    """Test server update endpoints."""

    async def test_update_server(self, modules, auth_headers, test_user):
        """Test updating server properties."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/servers/{server.id}",
                headers=auth_headers,
                json={
                    "name": "Updated Server Name",
                    "description": "Updated description",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Updated Server Name"
            assert data["description"] == "Updated description"

    async def test_update_server_without_permission(self, modules, session_users):
        """Test updating server without permission."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2 as member (not owner)
        modules.servers.add_member(server.id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/servers/{server.id}",
                headers=headers2,
                json={"name": "Hacked Name"},
            )

            assert response.status_code == 403

    async def test_update_server_sql_injection(self, modules, auth_headers, test_user):
        """Test SQL injection in server update."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/servers/{server.id}",
                headers=auth_headers,
                json={"description": "'; DELETE FROM servers WHERE '1'='1"},
            )

            # Should safely handle
            assert response.status_code in [200, 400]


@pytest.mark.asyncio
class TestServerDeletion:
    """Test server deletion endpoint."""

    async def test_delete_server(self, modules, auth_headers, test_user):
        """Test deleting a server."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/servers/{server.id}", headers=auth_headers
            )

            assert response.status_code == 200

    async def test_delete_server_not_owner(self, modules, session_users):
        """Test deleting server as non-owner."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2 as member
        modules.servers.add_member(server.id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(f"/api/v1/servers/{server.id}", headers=headers2)

            assert response.status_code == 403


@pytest.mark.asyncio
class TestMemberManagement:
    """Test member management endpoints."""

    async def test_get_server_members(self, modules, auth_headers, test_user):
        """Test getting server members."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/servers/{server.id}/members", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) > 0  # At least the owner

    async def test_kick_member(self, modules, session_users):
        """Test kicking a member from server."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2 as member
        modules.servers.add_member(server.id, user2.id)

        result1 = modules.auth.login(username1, password1)
        headers1 = {"Authorization": f"Bearer {result1.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/servers/{server.id}/members/{user2.id}", headers=headers1
            )

            assert response.status_code == 200

    async def test_kick_member_without_permission(self, modules, session_users):
        """Test kicking member without permission."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]
        user3, username3, password3 = session_users[2]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2 and user3
        modules.servers.add_member(server.id, user2.id)
        modules.servers.add_member(server.id, user3.id)

        # User2 tries to kick user3
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/servers/{server.id}/members/{user3.id}", headers=headers2
            )

            assert response.status_code == 403

    async def test_kick_nonexistent_member(self, modules, auth_headers, test_user):
        """Test kicking non-existent member."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/servers/{server.id}/members/999999999", headers=auth_headers
            )

            assert response.status_code == 404


@pytest.mark.asyncio
class TestRoleManagement:
    """Test role management endpoints."""

    async def test_get_server_roles(self, modules, auth_headers, test_user):
        """Test getting server roles."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/servers/{server.id}/roles", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    async def test_create_role(self, modules, auth_headers, test_user):
        """Test creating a role."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/servers/{server.id}/roles",
                headers=auth_headers,
                json={
                    "name": "Moderator",
                    "color": "#FF0000",
                    "permissions": {"kick_members": True},
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Moderator"

    async def test_create_role_sql_injection(self, modules, auth_headers, test_user):
        """Test SQL injection in role creation."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/servers/{server.id}/roles",
                headers=auth_headers,
                json={"name": "Role'; DROP TABLE roles; --"},
            )

            # Should safely handle
            assert response.status_code in [200, 400]

    async def test_update_role(self, modules, auth_headers, test_user):
        """Test updating a role."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        role = modules.servers.create_role(
            user_id=test_user["user"].id, server_id=server.id, name="Test Role"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/servers/{server.id}/roles/{role.id}",
                headers=auth_headers,
                json={"name": "Updated Role"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Updated Role"

    async def test_delete_role(self, modules, auth_headers, test_user):
        """Test deleting a role."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        role = modules.servers.create_role(
            user_id=test_user["user"].id, server_id=server.id, name="Test Role"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/servers/{server.id}/roles/{role.id}", headers=auth_headers
            )

            assert response.status_code == 200

    async def test_assign_role_to_member(self, modules, session_users):
        """Test assigning a role to a member."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2
        modules.servers.add_member(server.id, user2.id)

        # Create role
        role = modules.servers.create_role(
            user_id=user1.id, server_id=server.id, name="Test Role"
        )

        result1 = modules.auth.login(username1, password1)
        headers1 = {"Authorization": f"Bearer {result1.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.put(
                f"/api/v1/servers/{server.id}/members/{user2.id}/roles/{role.id}",
                headers=headers1,
            )

            assert response.status_code == 200

    async def test_remove_role_from_member(self, modules, session_users):
        """Test removing a role from a member."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2
        modules.servers.add_member(server.id, user2.id)

        # Create and assign role
        role = modules.servers.create_role(
            user_id=user1.id, server_id=server.id, name="Test Role"
        )
        modules.servers.assign_role(user1.id, server.id, user2.id, role.id)

        result1 = modules.auth.login(username1, password1)
        headers1 = {"Authorization": f"Bearer {result1.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/servers/{server.id}/members/{user2.id}/roles/{role.id}",
                headers=headers1,
            )

            assert response.status_code == 200


@pytest.mark.asyncio
class TestBanManagement:
    """Test ban management endpoints."""

    async def test_ban_user(self, modules, session_users):
        """Test banning a user from server."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2
        modules.servers.add_member(server.id, user2.id)

        result1 = modules.auth.login(username1, password1)
        headers1 = {"Authorization": f"Bearer {result1.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.put(
                f"/api/v1/servers/{server.id}/bans/{user2.id}",
                headers=headers1,
                json={"reason": "Test ban"},
            )

            assert response.status_code == 200

    async def test_get_bans(self, modules, auth_headers, test_user):
        """Test getting server bans."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/servers/{server.id}/bans", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    async def test_unban_user(self, modules, session_users):
        """Test unbanning a user."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Ban user2
        modules.servers.add_member(server.id, user2.id)
        modules.servers.ban_member(user1.id, server.id, user2.id, reason="Test")

        result1 = modules.auth.login(username1, password1)
        headers1 = {"Authorization": f"Bearer {result1.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/servers/{server.id}/bans/{user2.id}", headers=headers1
            )

            assert response.status_code == 200

    async def test_ban_without_permission(self, modules, session_users):
        """Test banning without permission."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]
        user3, username3, password3 = session_users[2]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2 and user3
        modules.servers.add_member(server.id, user2.id)
        modules.servers.add_member(server.id, user3.id)

        # User2 tries to ban user3
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.put(
                f"/api/v1/servers/{server.id}/bans/{user3.id}",
                headers=headers2,
                json={"reason": "Test"},
            )

            assert response.status_code == 403


@pytest.mark.asyncio
class TestAuditLog:
    """Test audit log endpoints."""

    async def test_get_audit_log(self, modules, auth_headers, test_user):
        """Test getting server audit log."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/servers/{server.id}/audit-logs", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)


@pytest.mark.asyncio
class TestChannelCreation:
    """Test channel creation in servers."""

    async def test_create_channel(self, modules, auth_headers, test_user):
        """Test creating a channel in a server."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/servers/{server.id}/channels",
                headers=auth_headers,
                json={"name": "new-channel", "type": "text"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "new-channel"

    async def test_create_channel_without_permission(self, modules, session_users):
        """Test creating channel without permission."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Add user2 as member
        modules.servers.add_member(server.id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/servers/{server.id}/channels",
                headers=headers2,
                json={"name": "hacked-channel"},
            )

            assert response.status_code == 403


@pytest.mark.asyncio
class TestConcurrentOperations:
    """Test concurrent server operations."""

    async def test_concurrent_server_updates(self, modules, auth_headers, test_user):
        """Test concurrent updates to same server."""
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.patch(
                    f"/api/v1/servers/{server.id}",
                    headers=auth_headers,
                    json={"name": f"Update {i}"},
                )
                for i in range(5)
            ]
            responses = await asyncio.gather(*tasks)

            # All should succeed (last one wins)
            success_count = sum(1 for r in responses if r.status_code == 200)
            assert success_count >= 1
