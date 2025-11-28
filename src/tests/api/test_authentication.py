"""
Tests for authentication middleware.
"""

import pytest
import uuid


class TestAuthenticationMiddleware:
    """Tests for authentication middleware."""

    def test_valid_bearer_token(self, test_client, auth_headers):
        """Test request with valid Bearer token."""
        response = test_client.get("/api/v1/users/@me", headers=auth_headers)
        
        assert response.status_code == 200

    def test_invalid_bearer_token(self, test_client):
        """Test request with invalid Bearer token."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer invalid_token_12345"}
        )
        
        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    def test_missing_authorization_header(self, test_client):
        """Test request without Authorization header."""
        response = test_client.get("/api/v1/users/@me")
        
        assert response.status_code == 401

    def test_malformed_authorization_header(self, test_client):
        """Test request with malformed Authorization header."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "malformed"}
        )
        
        assert response.status_code == 401

    def test_empty_bearer_token(self, test_client):
        """Test request with empty Bearer token."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer "}
        )
        
        assert response.status_code == 401

    def test_bot_token_scheme(self, test_client, db_and_modules):
        """Test request with Bot token scheme."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]
        
        user = auth.register(
            username=f"botowner_{unique_id}",
            email=f"botowner_{unique_id}@example.com",
            password="SecurePass123!"
        )
        
        bot = auth.create_bot(
            owner_id=user.id,
            username=f"testbot_{unique_id}",
            display_name=f"Test Bot {unique_id}"
        )
        
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bot {bot.token}"}
        )
        
        assert response.status_code == 200

    def test_case_insensitive_scheme(self, test_client, auth_headers):
        """Test that authorization scheme is case insensitive."""
        token = auth_headers["Authorization"].split()[1]
        
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"bearer {token}"}
        )
        
        assert response.status_code == 200

    def test_expired_token_handling(self, test_client):
        """Test handling of expired token."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer expired.token.here"}
        )
        
        assert response.status_code == 401
        data = response.json()
        assert "error" in data


class TestPublicEndpoints:
    """Tests for endpoints that don't require authentication."""

    def test_health_check_no_auth(self, test_client):
        """Test health check without authentication."""
        response = test_client.get("/api/v1/health")
        
        assert response.status_code == 200

    def test_root_endpoint_no_auth(self, test_client):
        """Test root endpoint without authentication."""
        response = test_client.get("/")
        
        assert response.status_code == 200

    def test_webhook_execute_no_auth(self, test_client, auth_headers, test_server):
        """Test webhook execution without user authentication."""
        channel_id = str(test_server["channel"].id)
        unique_id = uuid.uuid4().hex[:8]
        
        create_response = test_client.post(
            "/api/v1/webhooks",
            headers=auth_headers,
            json={
                "channel_id": channel_id,
                "name": f"No Auth Test {unique_id}"
            }
        )
        
        assert create_response.status_code == 200
        webhook_data = create_response.json()
        webhook_id = webhook_data["id"]
        token = webhook_data["token"].split(".")[-1]
        
        response = test_client.post(
            f"/api/v1/webhooks/{webhook_id}/{token}",
            json={"content": "No auth needed!"}
        )
        
        assert response.status_code == 200 or response.status_code == 204
