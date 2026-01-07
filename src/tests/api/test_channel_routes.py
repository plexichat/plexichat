"""
Tests for channel routes.
"""

import uuid
from src.core.servers.models import ChannelType


class TestGetChannel:
    """Tests for GET /channels/{channel_id} endpoint."""

    def test_get_channel_success(self, test_client, auth_headers, test_server):
        """Test getting channel by ID."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(
            f"/api/v1/channels/{channel_id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == channel_id
        assert "name" in data
        assert "channel_type" in data

    def test_get_nonexistent_channel(self, test_client, auth_headers):
        """Test getting nonexistent channel."""
        response = test_client.get(
            "/api/v1/channels/999999999999999999", headers=auth_headers
        )

        assert response.status_code == 404

    def test_get_channel_invalid_id(self, test_client, auth_headers):
        """Test getting channel with invalid ID."""
        response = test_client.get("/api/v1/channels/invalid_id", headers=auth_headers)

        assert response.status_code == 400

    def test_get_channel_without_auth(self, test_client, test_server):
        """Test getting channel without authentication."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(f"/api/v1/channels/{channel_id}")

        assert response.status_code == 401


class TestUpdateChannel:
    """Tests for PATCH /channels/{channel_id} endpoint."""

    def test_update_channel_name(self, test_client, auth_headers, test_server):
        """Test updating channel name."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.patch(
            f"/api/v1/channels/{channel_id}",
            headers=auth_headers,
            json={"name": f"updated-channel-{unique_id}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == f"updated-channel-{unique_id}"

    def test_update_channel_topic(self, test_client, auth_headers, test_server):
        """Test updating channel topic."""
        channel_id = str(test_server["channel"].id)

        response = test_client.patch(
            f"/api/v1/channels/{channel_id}",
            headers=auth_headers,
            json={"topic": "New channel topic"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["topic"] == "New channel topic"

    def test_update_channel_nsfw(self, test_client, auth_headers, test_server):
        """Test updating channel NSFW flag."""
        channel_id = str(test_server["channel"].id)

        response = test_client.patch(
            f"/api/v1/channels/{channel_id}", headers=auth_headers, json={"nsfw": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["nsfw"] is True


class TestDeleteChannel:
    """Tests for DELETE /channels/{channel_id} endpoint."""

    def test_delete_channel_success(self, test_client, db_and_modules):
        """Test deleting a channel."""
        auth = db_and_modules["auth"]
        servers = db_and_modules["servers"]
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"deletechan_{unique_id}",
            email=f"deletechan_{unique_id}@example.com",
            password="SecurePass123!",
        )

        result = auth.login(
            username=f"deletechan_{unique_id}", password="SecurePass123!"
        )

        server = servers.create_server(user.id, f"Channel Delete Test {unique_id}")
        channel = servers.create_channel(
            user_id=user.id,
            server_id=server.id,
            name=f"to-delete-{unique_id}",
            channel_type=ChannelType.TEXT,
        )

        response = test_client.delete(
            f"/api/v1/channels/{channel.id}",
            headers={"Authorization": f"Bearer {result.token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_delete_nonexistent_channel(self, test_client, auth_headers):
        """Test deleting nonexistent channel."""
        response = test_client.delete(
            "/api/v1/channels/999999999999999999", headers=auth_headers
        )

        assert response.status_code == 404


class TestChannelFields:
    """Tests for channel response fields."""

    def test_channel_has_server_id(self, test_client, auth_headers, test_server):
        """Test that channel response includes server_id."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(
            f"/api/v1/channels/{channel_id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "server_id" in data
        assert data["server_id"] == str(test_server["server"].id)

    def test_channel_has_channel_type(self, test_client, auth_headers, test_server):
        """Test that channel response includes channel_type."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(
            f"/api/v1/channels/{channel_id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "channel_type" in data

    def test_channel_has_created_at(self, test_client, auth_headers, test_server):
        """Test that channel response includes created_at."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(
            f"/api/v1/channels/{channel_id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "created_at" in data
        assert data["created_at"] > 0
