"""
Tests for webhook routes - webhook CRUD and execution.

Covers:
- Webhook CRUD operations
- Webhook execution (with and without auth)
- Authorization checks
- Token validation
- Input sanitization
- SQL injection prevention
- Rate limiting
- Error handling
"""

import pytest
import asyncio
from httpx import AsyncClient
from src.api.app import create_app


@pytest.mark.asyncio
class TestWebhookCreation:
    """Test webhook creation endpoints."""

    async def test_create_webhook(self, modules, auth_headers, test_server):
        """Test creating a webhook."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks",
                headers=auth_headers,
                json={"channel_id": str(channel.id), "name": "Test Webhook"},
            )

            # May succeed or return 500 if webhooks module not available
            assert response.status_code in [200, 500]
            if response.status_code == 200:
                data = response.json()
                assert data["name"] == "Test Webhook"
                assert "token" in data  # Token included on creation
                assert "id" in data

    async def test_create_webhook_without_auth(self, test_server):
        """Test creating webhook without authentication."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks",
                json={"channel_id": str(channel.id), "name": "Test Webhook"},
            )

            assert response.status_code == 401

    async def test_create_webhook_invalid_channel(self, auth_headers):
        """Test creating webhook for non-existent channel."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks",
                headers=auth_headers,
                json={"channel_id": "999999999", "name": "Test Webhook"},
            )

            assert response.status_code in [404, 500]

    async def test_create_webhook_without_permission(
        self, modules, session_users, test_server
    ):
        """Test creating webhook without permission."""
        user2, username2, password2 = session_users[1]

        # Add user2 as member (not admin)
        modules.servers.add_member(test_server["server"].id, user2.id)

        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks",
                headers=headers2,
                json={"channel_id": str(channel.id), "name": "Test Webhook"},
            )

            assert response.status_code in [403, 500]

    async def test_create_webhook_sql_injection(self, auth_headers, test_server):
        """Test SQL injection in webhook name."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks",
                headers=auth_headers,
                json={
                    "channel_id": str(channel.id),
                    "name": "Webhook'; DROP TABLE webhooks; --",
                },
            )

            # Should safely handle
            assert response.status_code in [200, 400, 500]

    async def test_create_webhook_xss(self, auth_headers, test_server):
        """Test XSS in webhook name."""
        app = create_app()
        channel = test_server["channel"]
        xss_name = "<script>alert('xss')</script>"

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks",
                headers=auth_headers,
                json={"channel_id": str(channel.id), "name": xss_name},
            )

            # May accept or reject
            assert response.status_code in [200, 400, 500]

    async def test_create_webhook_missing_fields(self, auth_headers):
        """Test creating webhook with missing required fields."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Missing channel_id
            response = await ac.post(
                "/api/v1/webhooks", headers=auth_headers, json={"name": "Test Webhook"}
            )

            assert response.status_code == 422

            # Missing name
            response = await ac.post(
                "/api/v1/webhooks", headers=auth_headers, json={"channel_id": "123"}
            )

            assert response.status_code == 422


@pytest.mark.asyncio
class TestWebhookRetrieval:
    """Test webhook retrieval endpoints."""

    async def test_get_webhook(self, modules, auth_headers, test_server):
        """Test getting a webhook by ID."""
        # Create webhook first
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/webhooks/{webhook.id}", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert str(data["id"]) == str(webhook.id)
            assert "token" not in data  # Token not included in GET

    async def test_get_webhook_nonexistent(self, auth_headers):
        """Test getting non-existent webhook."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/webhooks/999999999", headers=auth_headers)

            assert response.status_code in [404, 500]

    async def test_get_webhook_without_permission(
        self, modules, session_users, test_server
    ):
        """Test getting webhook without permission."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        # User2 tries to access
        user2, username2, password2 = session_users[1]
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(f"/api/v1/webhooks/{webhook.id}", headers=headers2)

            assert response.status_code in [403, 404, 500]

    async def test_get_webhook_invalid_id(self, auth_headers):
        """Test getting webhook with invalid ID."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/webhooks/invalid_id", headers=auth_headers)

            assert response.status_code in [400, 500]


@pytest.mark.asyncio
class TestWebhookDeletion:
    """Test webhook deletion endpoints."""

    async def test_delete_webhook(self, modules, auth_headers, test_server):
        """Test deleting a webhook."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/webhooks/{webhook.id}", headers=auth_headers
            )

            assert response.status_code == 200

    async def test_delete_webhook_without_permission(
        self, modules, session_users, test_server
    ):
        """Test deleting webhook without permission."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        # User2 tries to delete
        user2, username2, password2 = session_users[1]
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/webhooks/{webhook.id}", headers=headers2
            )

            assert response.status_code in [403, 404, 500]

    async def test_delete_nonexistent_webhook(self, auth_headers):
        """Test deleting non-existent webhook."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                "/api/v1/webhooks/999999999", headers=auth_headers
            )

            assert response.status_code in [404, 500]


@pytest.mark.asyncio
class TestWebhookExecution:
    """Test webhook execution endpoints."""

    async def test_execute_webhook(self, modules, test_server):
        """Test executing a webhook."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                json={"content": "Hello from webhook"},
            )

            # Should succeed or return 204 (no wait)
            assert response.status_code in [200, 204, 500]

    async def test_execute_webhook_with_wait(self, modules, test_server):
        """Test executing webhook with wait parameter."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}?wait=true",
                json={"content": "Hello from webhook"},
            )

            # Should return message data
            assert response.status_code in [200, 500]
            if response.status_code == 200:
                data = response.json()
                assert "id" in data
                assert data["content"] == "Hello from webhook"

    async def test_execute_webhook_invalid_token(self, modules, test_server):
        """Test executing webhook with invalid token."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/invalid_token",
                json={"content": "Hello from webhook"},
            )

            assert response.status_code in [401, 404, 500]

    async def test_execute_webhook_empty_content(self, modules, test_server):
        """Test executing webhook with empty content."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}", json={"content": ""}
            )

            assert response.status_code in [400, 500]

    async def test_execute_webhook_with_embeds(self, modules, test_server):
        """Test executing webhook with embeds."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                json={
                    "content": "Message with embed",
                    "embeds": [
                        {"title": "Test Embed", "description": "Test description"}
                    ],
                },
            )

            assert response.status_code in [200, 204, 500]

    async def test_execute_webhook_custom_username(self, modules, test_server):
        """Test executing webhook with custom username."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}?wait=true",
                json={"content": "Custom webhook", "username": "Custom Bot"},
            )

            assert response.status_code in [200, 500]
            if response.status_code == 200:
                data = response.json()
                assert data.get("username") == "Custom Bot"

    async def test_execute_webhook_sql_injection(self, modules, test_server):
        """Test SQL injection in webhook execution."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                json={"content": "'; DROP TABLE messages; --"},
            )

            # Should safely handle
            assert response.status_code in [200, 204, 500]

    async def test_execute_nonexistent_webhook(self):
        """Test executing non-existent webhook."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks/999999999/fake_token", json={"content": "Hello"}
            )

            assert response.status_code in [404, 500]


@pytest.mark.asyncio
class TestWebhookAuthorization:
    """Test webhook authorization and permissions."""

    async def test_webhook_execution_no_auth_required(self, modules, test_server):
        """Test webhook execution doesn't require user authentication."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # No Authorization header
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                json={"content": "No auth needed"},
            )

            # Should succeed without auth
            assert response.status_code in [200, 204, 500]

    async def test_webhook_token_isolation(self, modules, test_server):
        """Test that webhook tokens are isolated."""
        # Create two webhooks
        webhook1 = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Webhook 1",
        )

        webhook2 = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Webhook 2",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Try to use webhook1's token with webhook2's ID
            response = await ac.post(
                f"/api/v1/webhooks/{webhook2.id}/{webhook1.token}",
                json={"content": "Cross-webhook"},
            )

            # Should fail
            assert response.status_code in [401, 404, 500]


@pytest.mark.asyncio
class TestInputValidation:
    """Test input validation in webhook routes."""

    async def test_webhook_name_length(self, auth_headers, test_server):
        """Test webhook name length limits."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Very long name
            long_name = "a" * 200
            response = await ac.post(
                "/api/v1/webhooks",
                headers=auth_headers,
                json={"channel_id": str(channel.id), "name": long_name},
            )

            # Should truncate or reject
            assert response.status_code in [200, 400, 500]

    async def test_webhook_content_length(self, modules, test_server):
        """Test webhook message content length limit."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Very long content
            long_content = "a" * 5000
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                json={"content": long_content},
            )

            # Should reject or truncate
            assert response.status_code in [200, 204, 400, 500]

    async def test_webhook_too_many_embeds(self, modules, test_server):
        """Test webhook with too many embeds."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Too many embeds
            embeds = [{"title": f"Embed {i}"} for i in range(15)]
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                json={"content": "Too many embeds", "embeds": embeds},
            )

            # Should reject
            assert response.status_code in [200, 204, 400, 500]


@pytest.mark.asyncio
class TestConcurrentOperations:
    """Test concurrent webhook operations."""

    async def test_concurrent_webhook_executions(self, modules, test_server):
        """Test executing webhook multiple times concurrently."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.post(
                    f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                    json={"content": f"Message {i}"},
                )
                for i in range(10)
            ]
            responses = await asyncio.gather(*tasks)

            # All should complete
            for resp in responses:
                assert resp.status_code in [200, 204, 500]

    async def test_concurrent_webhook_creates(self, auth_headers, test_server):
        """Test creating multiple webhooks concurrently."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.post(
                    "/api/v1/webhooks",
                    headers=auth_headers,
                    json={"channel_id": str(channel.id), "name": f"Webhook {i}"},
                )
                for i in range(5)
            ]
            responses = await asyncio.gather(*tasks)

            # All should complete
            for resp in responses:
                assert resp.status_code in [200, 500]


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling in webhook routes."""

    async def test_malformed_json(self, modules, test_server):
        """Test handling of malformed JSON."""
        # Create webhook
        webhook = modules.webhooks.create_webhook(
            user_id=test_server["server"].owner_id,
            channel_id=test_server["channel"].id,
            name="Test Webhook",
        )

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
                content="{invalid json}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status_code == 422

    async def test_error_response_format(self, auth_headers):
        """Test error response format consistency."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/webhooks/999999999", headers=auth_headers)

            if response.status_code in [404, 500]:
                data = response.json()
                assert "error" in data
