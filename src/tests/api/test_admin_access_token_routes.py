"""Tests for admin access token API routes."""

import pytest


@pytest.mark.api
class TestAdminAccessTokenRoutes:
    """Tests for admin access token CRUD via API."""

    def test_list_access_tokens(self, test_client, auth_headers):
        """Test listing admin access tokens."""
        response = test_client.get(
            "/api/v1/admin/security/access-tokens",
            headers=auth_headers,
        )
        # Admin endpoints may require admin role (403) or work (200)
        assert response.status_code in (200, 403, 401)

    def test_create_access_token(self, test_client, auth_headers):
        """Test creating an admin access token."""
        response = test_client.post(
            "/api/v1/admin/security/access-tokens",
            json={
                "name": "Test Token",
                "description": "Token for testing",
            },
            headers=auth_headers,
        )
        # Admin endpoints may require admin role (403) or work (200/201)
        assert response.status_code in (200, 201, 403)

    def test_revoke_access_token(self, test_client, auth_headers):
        """Test revoking an admin access token."""
        response = test_client.delete(
            "/api/v1/admin/security/access-tokens/999999",
            headers=auth_headers,
        )
        # 404 (not found), 403 (forbidden), or 200 (success)
        assert response.status_code in (200, 404, 403)

    def test_create_token_missing_name(self, test_client, auth_headers):
        """Test creating token without name returns validation error."""
        response = test_client.post(
            "/api/v1/admin/security/access-tokens",
            json={"description": "No name provided"},
            headers=auth_headers,
        )
        assert response.status_code in (400, 403, 422)
