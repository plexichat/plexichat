"""
Tests for message routes.
"""


class TestGetMessages:
    """Tests for GET /channels/{channel_id}/messages endpoint."""

    def test_get_messages_success(self, test_client, auth_headers, test_server):
        """Test getting channel messages."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(
            f"/api/v1/channels/{channel_id}/messages", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_messages_with_limit(self, test_client, auth_headers, test_server):
        """Test getting messages with limit."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(
            f"/api/v1/channels/{channel_id}/messages?limit=10", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 10

    def test_get_messages_nonexistent_channel(self, test_client, auth_headers):
        """Test getting messages from nonexistent channel."""
        response = test_client.get(
            "/api/v1/channels/999999999999999999/messages", headers=auth_headers
        )

        assert response.status_code == 404

    def test_get_messages_without_auth(self, test_client, test_server):
        """Test getting messages without authentication."""
        channel_id = str(test_server["channel"].id)

        response = test_client.get(f"/api/v1/channels/{channel_id}/messages")

        assert response.status_code == 401


class TestSendMessage:
    """Tests for POST /channels/{channel_id}/messages endpoint."""

    def test_send_message_success(self, test_client, auth_headers, test_server):
        """Test sending a message."""
        channel_id = str(test_server["channel"].id)

        response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Hello, world!"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "Hello, world!"
        assert "id" in data
        assert "author_id" in data
        assert "created_at" in data

    def test_send_empty_message(self, test_client, auth_headers, test_server):
        """Test sending empty message."""
        channel_id = str(test_server["channel"].id)

        response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": ""},
        )

        assert response.status_code == 400

    def test_send_message_nonexistent_channel(self, test_client, auth_headers):
        """Test sending message to nonexistent channel."""
        response = test_client.post(
            "/api/v1/channels/999999999999999999/messages",
            headers=auth_headers,
            json={"content": "Test message"},
        )

        assert response.status_code == 404

    def test_send_message_without_auth(self, test_client, test_server):
        """Test sending message without authentication."""
        channel_id = str(test_server["channel"].id)

        response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            json={"content": "Unauthorized message"},
        )

        assert response.status_code == 401


class TestEditMessage:
    """Tests for PATCH /channels/{channel_id}/messages/{message_id} endpoint."""

    def test_edit_message_success(
        self, test_client, auth_headers, test_server, db_and_modules
    ):
        """Test editing a message."""
        db_and_modules["servers"]
        channel_id = str(test_server["channel"].id)

        send_response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Original message"},
        )

        assert send_response.status_code == 200
        message_id = send_response.json()["id"]

        response = test_client.patch(
            f"/api/v1/channels/{channel_id}/messages/{message_id}",
            headers=auth_headers,
            json={"content": "Edited message"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "Edited message"


class TestDeleteMessage:
    """Tests for DELETE /channels/{channel_id}/messages/{message_id} endpoint."""

    def test_delete_message_success(self, test_client, auth_headers, test_server):
        """Test deleting a message."""
        channel_id = str(test_server["channel"].id)

        send_response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Message to delete"},
        )

        assert send_response.status_code == 200
        message_id = send_response.json()["id"]

        response = test_client.delete(
            f"/api/v1/channels/{channel_id}/messages/{message_id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_delete_nonexistent_message(self, test_client, auth_headers, test_server):
        """Test deleting nonexistent message."""
        channel_id = str(test_server["channel"].id)

        response = test_client.delete(
            f"/api/v1/channels/{channel_id}/messages/999999999999999999",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestMessageFields:
    """Tests for message response fields."""

    def test_message_has_author_id(self, test_client, auth_headers, test_server):
        """Test that message response includes author_id."""
        channel_id = str(test_server["channel"].id)

        response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Test author field"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "author_id" in data
        assert data["author_id"] is not None

    def test_message_has_created_at(self, test_client, auth_headers, test_server):
        """Test that message response includes created_at."""
        channel_id = str(test_server["channel"].id)

        response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Test timestamp field"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "created_at" in data
        assert data["created_at"] > 0

    def test_message_has_channel_id(self, test_client, auth_headers, test_server):
        """Test that message response includes channel_id."""
        channel_id = str(test_server["channel"].id)

        response = test_client.post(
            f"/api/v1/channels/{channel_id}/messages",
            headers=auth_headers,
            json={"content": "Test channel field"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "channel_id" in data
