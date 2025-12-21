"""
Tests for webhook routes.
"""

import uuid


class TestCreateWebhook:
    """Tests for POST /webhooks endpoint."""

    def test_create_webhook_success(self, test_client, auth_headers, test_server):
        """Test creating a webhook."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Test Webhook {unique_id}"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == f"Test Webhook {unique_id}"
        assert data["channel_id"] == channel_id
        assert "id" in data
        assert "token" in data
        assert data["token"] is not None

    def test_create_webhook_with_avatar(self, test_client, auth_headers, test_server):
        """Test creating a webhook with avatar."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Avatar Webhook {unique_id}",
                "avatar_url": "https://example.com/avatar.png"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["avatar_url"] == "https://example.com/avatar.png"

    def test_create_webhook_invalid_channel(self, test_client, auth_headers):
        """Test creating webhook for invalid channel."""
        response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": "999999999999999999",
                "name": "Invalid Channel Webhook"
            }
        )

        assert response.status_code == 404

    def test_create_webhook_without_auth(self, test_client, test_server):
        """Test creating webhook without authentication."""
        channel_id = str(test_server["channel"].id)

        response = test_client.post(
            "/api/v1/webhooks",
            json={
                "channel_id": channel_id,
                "name": "Unauthorized Webhook"
            }
        )

        assert response.status_code == 401


class TestGetWebhook:
    """Tests for GET /webhooks/{webhook_id} endpoint."""

    def test_get_webhook_success(self, test_client, auth_headers, test_server, db_and_modules, test_user):
        """Test getting a webhook."""
        webhooks = db_and_modules["webhooks"]
        channel_id = test_server["channel"].id
        unique_id = uuid.uuid4().hex[:8]

        webhook = webhooks.create_webhook(
            user_id=test_user["user"].id,
            channel_id=channel_id,
            name=f"Get Test {unique_id}"
        )

        response = test_client.get(
            f"/api/v1/webhooks/{webhook.id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(webhook.id)
        assert "token" not in data or data["token"] is None

    def test_get_nonexistent_webhook(self, test_client, auth_headers):
        """Test getting nonexistent webhook."""
        response = test_client.get(
            "/api/v1/webhooks/999999999999999999",
            headers=auth_headers
        )

        assert response.status_code == 404


class TestDeleteWebhook:
    """Tests for DELETE /webhooks/{webhook_id} endpoint."""

    def test_delete_webhook_success(self, test_client, auth_headers, test_server):
        """Test deleting a webhook."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        create_response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Delete Test {unique_id}"
            }
        )

        assert create_response.status_code == 200
        webhook_id = create_response.json()["id"]

        response = test_client.delete(
            f"/api/v1/webhooks/{webhook_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestExecuteWebhook:
    """Tests for POST /webhooks/{webhook_id}/{token} endpoint."""

    def test_execute_webhook_success(self, test_client, auth_headers, test_server):
        """Test executing a webhook."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        create_response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Execute Test {unique_id}"
            }
        )

        assert create_response.status_code == 200
        webhook_data = create_response.json()
        webhook_id = webhook_data["id"]
        token = webhook_data["token"]

        token_secret = token.split(".")[-1]

        response = test_client.post(
            f"/api/v1/webhooks/{webhook_id}/{token_secret}",
            json={"content": "Hello from webhook!"}
        )

        assert response.status_code == 200 or response.status_code == 204

    def test_execute_webhook_invalid_token(self, test_client, auth_headers, test_server):
        """Test executing webhook with invalid token."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        create_response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Invalid Token Test {unique_id}"
            }
        )

        assert create_response.status_code == 200
        webhook_id = create_response.json()["id"]

        response = test_client.post(
            f"/api/v1/webhooks/{webhook_id}/invalid_token",
            json={"content": "Should fail"}
        )

        assert response.status_code == 401

    def test_execute_webhook_empty_content(self, test_client, auth_headers, test_server):
        """Test executing webhook with empty content."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        create_response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Empty Content Test {unique_id}"
            }
        )

        assert create_response.status_code == 200
        webhook_data = create_response.json()
        webhook_id = webhook_data["id"]
        token = webhook_data["token"].split(".")[-1]

        response = test_client.post(
            f"/api/v1/webhooks/{webhook_id}/{token}",
            json={"content": ""}
        )

        assert response.status_code == 400


class TestWebhookFields:
    """Tests for webhook response fields."""

    def test_webhook_has_server_id(self, test_client, auth_headers, test_server):
        """Test that webhook response includes server_id."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Server ID Test {unique_id}"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "server_id" in data
        assert data["server_id"] == str(test_server["server"].id)

    def test_webhook_has_created_at(self, test_client, auth_headers, test_server):
        """Test that webhook response includes created_at."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"Created At Test {unique_id}"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "created_at" in data
        assert data["created_at"] > 0

    def test_webhook_url_format(self, test_client, auth_headers, test_server):
        """Test that webhook URL has correct format."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]

        response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"URL Format Test {unique_id}"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert "/webhooks/" in data["url"]
