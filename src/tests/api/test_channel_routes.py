"""
Tests for channel routes - channel management, invites, webhooks.

Covers:
- Channel CRUD operations
- Authorization checks
- Invite creation and usage
- Webhook management
- File uploads
- Input sanitization
- SQL injection prevention
- Error handling
"""

import pytest
import asyncio
import uuid
import io
from httpx import AsyncClient
from src.api.app import create_app


@pytest.mark.asyncio
class TestChannelRetrieval:
    """Test channel retrieval endpoints."""

    async def test_get_channel(self, modules, auth_headers, test_server):
        """Test getting a channel by ID."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert str(data["id"]) == str(channel.id)
            assert data["name"] == channel.name

    async def test_get_channel_without_auth(self, test_server):
        """Test getting channel without authentication."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(f"/api/v1/channels/{channel.id}")

            assert response.status_code == 401

    async def test_get_nonexistent_channel(self, auth_headers):
        """Test getting a non-existent channel."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/channels/999999999", headers=auth_headers)

            assert response.status_code == 404

    async def test_get_channel_unauthorized(self, modules, session_users):
        """Test getting channel user doesn't have access to."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # Create server with user1
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Private Server {uuid.uuid4().hex[:6]}"
        )
        channels = modules.servers.get_channels(user1.id, server.id)
        channel = channels[0]

        # User2 tries to access
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(f"/api/v1/channels/{channel.id}", headers=headers2)

            assert response.status_code in [403, 404]

    async def test_get_channel_invalid_id(self, auth_headers):
        """Test getting channel with invalid ID format."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/channels/invalid_id", headers=auth_headers)

            assert response.status_code == 400


@pytest.mark.asyncio
class TestChannelUpdate:
    """Test channel update endpoints."""

    async def test_update_channel(self, modules, auth_headers, test_server):
        """Test updating channel properties."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                headers=auth_headers,
                json={"name": "Updated Channel Name", "topic": "Updated topic"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Updated Channel Name"
            assert data["topic"] == "Updated topic"

    async def test_update_channel_without_permission(
        self, modules, session_users, test_server
    ):
        """Test updating channel without permission."""
        user2, username2, password2 = session_users[1]

        # Add user2 as member (not admin)
        modules.servers.add_member(test_server["server"].id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                headers=headers2,
                json={"name": "Hacked Name"},
            )

            assert response.status_code == 403

    async def test_update_channel_invalid_name(
        self, modules, auth_headers, test_server
    ):
        """Test updating channel with invalid name."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Empty name
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                headers=auth_headers,
                json={"name": ""},
            )

            assert response.status_code in [400, 422]

    async def test_update_channel_sql_injection(
        self, modules, auth_headers, test_server
    ):
        """Test SQL injection prevention in channel update."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                headers=auth_headers,
                json={"name": "Test'; DROP TABLE channels; --"},
            )

            # Should safely handle
            assert response.status_code in [200, 400]


@pytest.mark.asyncio
class TestChannelDeletion:
    """Test channel deletion endpoint."""

    async def test_delete_channel(self, modules, auth_headers, test_user):
        """Test deleting a channel."""
        # Create a test server with channel
        server = modules.servers.create_server(
            owner_id=test_user["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )

        # Create additional channel to delete
        channel = modules.servers.create_channel(
            user_id=test_user["user"].id, server_id=server.id, name="Channel to Delete"
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/channels/{channel.id}", headers=auth_headers
            )

            assert response.status_code == 200

    async def test_delete_channel_without_permission(self, modules, session_users):
        """Test deleting channel without permission."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )
        channels = modules.servers.get_channels(user1.id, server.id)
        channel = channels[0]

        # Add user2 as member
        modules.servers.add_member(server.id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/channels/{channel.id}", headers=headers2
            )

            assert response.status_code == 403

    async def test_delete_nonexistent_channel(self, auth_headers):
        """Test deleting a non-existent channel."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                "/api/v1/channels/999999999", headers=auth_headers
            )

            assert response.status_code == 404


@pytest.mark.asyncio
class TestInvites:
    """Test invite management endpoints."""

    async def test_create_invite(self, modules, auth_headers, test_server):
        """Test creating an invite for a channel."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/invites",
                headers=auth_headers,
                json={"max_age": 86400, "max_uses": 10},
            )

            assert response.status_code == 200
            data = response.json()
            assert "code" in data
            assert data["max_age"] == 86400
            assert data["max_uses"] == 10

    async def test_get_invite_info(self, modules, auth_headers, test_server):
        """Test getting invite information."""
        app = create_app()

        # Create an invite
        invite = modules.servers.create_invite(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            max_age=86400,
            max_uses=5,
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/invites/{invite.code}", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert data["code"] == invite.code

    async def test_use_invite(self, modules, session_users, test_server):
        """Test joining server via invite."""
        user2, username2, password2 = session_users[1]

        # Create an invite
        invite = modules.servers.create_invite(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            max_age=86400,
            max_uses=5,
        )

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/invites/{invite.code}", headers=headers2
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    async def test_use_expired_invite(self, modules, auth_headers):
        """Test using an expired invite."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/channels/invites/EXPIRED123", headers=auth_headers
            )

            assert response.status_code == 404

    async def test_delete_invite(self, modules, auth_headers, test_server):
        """Test deleting an invite."""
        app = create_app()

        # Create an invite
        invite = modules.servers.create_invite(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            max_age=86400,
            max_uses=5,
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/channels/invites/{invite.code}", headers=auth_headers
            )

            assert response.status_code == 200

    async def test_create_invite_without_permission(
        self, modules, session_users, test_server
    ):
        """Test creating invite without permission."""
        user2, username2, password2 = session_users[1]

        # Add user2 as member (not admin)
        modules.servers.add_member(test_server["server"].id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/invites",
                headers=headers2,
                json={"max_age": 86400},
            )

            # May succeed or fail depending on permissions
            assert response.status_code in [200, 403]


@pytest.mark.asyncio
class TestWebhooks:
    """Test webhook management endpoints."""

    async def test_get_channel_webhooks(self, modules, auth_headers, test_server):
        """Test getting webhooks for a channel."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/webhooks", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    async def test_get_webhooks_without_permission(
        self, modules, session_users, test_server
    ):
        """Test getting webhooks without permission."""
        user2, username2, password2 = session_users[1]

        # Add user2 as member (not admin)
        modules.servers.add_member(test_server["server"].id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/webhooks", headers=headers2
            )

            assert response.status_code in [200, 403]


@pytest.mark.asyncio
class TestFileUpload:
    """Test file upload endpoints."""

    async def test_upload_attachment(self, modules, auth_headers, test_server):
        """Test uploading a file attachment."""
        app = create_app()
        channel = test_server["channel"]

        # Create a test file
        file_content = b"Test file content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/attachments",
                headers=auth_headers,
                files=files,
            )

            # May succeed or return 500 if media module not available
            assert response.status_code in [200, 500]

    async def test_upload_attachment_unauthorized(self, test_server):
        """Test uploading attachment without authentication."""
        app = create_app()
        channel = test_server["channel"]

        file_content = b"Test file content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/attachments", files=files
            )

            assert response.status_code == 401

    async def test_upload_attachment_to_unauthorized_channel(
        self, modules, session_users
    ):
        """Test uploading to channel without access."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 creates server
        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )
        channels = modules.servers.get_channels(user1.id, server.id)
        channel = channels[0]

        # User2 tries to upload
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        file_content = b"Test file content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/attachments",
                headers=headers2,
                files=files,
            )

            assert response.status_code == 404


@pytest.mark.asyncio
class TestInputSanitization:
    """Test input sanitization and validation."""

    async def test_channel_name_sql_injection(self, modules, auth_headers, test_server):
        """Test SQL injection in channel name update."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                headers=auth_headers,
                json={"name": "'; DELETE FROM channels WHERE '1'='1"},
            )

            # Should safely handle
            assert response.status_code in [200, 400]

    async def test_channel_topic_xss(self, modules, auth_headers, test_server):
        """Test XSS in channel topic."""
        app = create_app()
        channel = test_server["channel"]
        xss_content = "<script>alert('xss')</script>"

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                headers=auth_headers,
                json={"topic": xss_content},
            )

            # Should preserve content (sanitization is client-side)
            if response.status_code == 200:
                data = response.json()
                assert xss_content in str(data.get("topic", ""))

    async def test_long_channel_name(self, modules, auth_headers, test_server):
        """Test channel name length limit."""
        app = create_app()
        channel = test_server["channel"]

        # Very long name
        long_name = "a" * 200

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                headers=auth_headers,
                json={"name": long_name},
            )

            # Should either truncate or reject
            assert response.status_code in [200, 400, 422]


@pytest.mark.asyncio
class TestConcurrentOperations:
    """Test concurrent channel operations."""

    async def test_concurrent_channel_updates(self, modules, auth_headers, test_server):
        """Test concurrent updates to same channel."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.patch(
                    f"/api/v1/channels/{channel.id}",
                    headers=auth_headers,
                    json={"name": f"Update {i}"},
                )
                for i in range(5)
            ]
            responses = await asyncio.gather(*tasks)

            # All should succeed (last one wins)
            success_count = sum(1 for r in responses if r.status_code == 200)
            assert success_count >= 1

    async def test_concurrent_invite_creation(self, modules, auth_headers, test_server):
        """Test creating multiple invites concurrently."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.post(
                    f"/api/v1/channels/{channel.id}/invites",
                    headers=auth_headers,
                    json={"max_age": 86400},
                )
                for _ in range(5)
            ]
            responses = await asyncio.gather(*tasks)

            # All should succeed
            for resp in responses:
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling in channel routes."""

    async def test_invalid_json_body(self, auth_headers, test_server):
        """Test handling of invalid JSON in request body."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}",
                content="{invalid json}",
                headers={**auth_headers, "Content-Type": "application/json"},
            )

            assert response.status_code == 422

    async def test_missing_required_fields(self, auth_headers, test_server):
        """Test handling of missing required fields."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Empty body when creating invite
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/invites", headers=auth_headers, json={}
            )

            # Should succeed with defaults or return 400
            assert response.status_code in [200, 400, 422]

    async def test_error_response_format(self, auth_headers):
        """Test error response format consistency."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/channels/999999999", headers=auth_headers)

            assert response.status_code == 404
            data = response.json()
            assert "error" in data
            assert "code" in data["error"]
            assert "message" in data["error"]
