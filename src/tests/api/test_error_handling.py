"""
Tests for error handling middleware.
"""

import uuid


class TestErrorResponses:
    """Tests for error response format."""

    def test_404_error_format(self, test_client, auth_headers):
        """Test 404 error response format."""
        response = test_client.get(
            "/api/v1/users/999999999999999999",
            headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == 404

    def test_401_error_format(self, test_client):
        """Test 401 error response format."""
        response = test_client.get("/api/v1/users/@me")

        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == 401

    def test_400_error_format(self, test_client, auth_headers):
        """Test 400 error response format."""
        response = test_client.get(
            "/api/v1/users/invalid_id",
            headers=auth_headers
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert data["error"]["code"] == 400

    def test_validation_error_format(self, test_client, auth_headers):
        """Test validation error response format."""
        response = test_client.post(
            "/api/v1/servers",
            headers=auth_headers,
            json={"name": ""}
        )

        assert response.status_code == 400 or response.status_code == 422
        data = response.json()
        assert "error" in data or "detail" in data


class TestHTTPExceptions:
    """Tests for HTTP exception handling."""

    def test_method_not_allowed(self, test_client, auth_headers):
        """Test method not allowed response."""
        response = test_client.put("/api/v1/health", headers=auth_headers)

        assert response.status_code == 405

    def test_not_found_route(self, test_client, auth_headers):
        """Test not found route response."""
        response = test_client.get(
            "/api/v1/nonexistent/route",
            headers=auth_headers
        )

        assert response.status_code == 404


class TestConflictErrors:
    """Tests for conflict (409) error handling."""

    def test_duplicate_username_error(self, test_client, test_user):
        """Test duplicate username returns 409."""
        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": test_user["username"],
                "email": "different@example.com",
                "password": "SecurePass123!"
            }
        )

        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 409


class TestForbiddenErrors:
    """Tests for forbidden (403) error handling."""

    def test_permission_denied_error(self, test_client, db_and_modules, test_server):
        """Test permission denied returns 403."""
        auth = db_and_modules["auth"]
        unique_id = uuid.uuid4().hex[:8]

        other_user = auth.register(
            username=f"otheruser_{unique_id}",
            email=f"otheruser_{unique_id}@example.com",
            password="SecurePass123!"
        )

        result = auth.login(
            username=f"otheruser_{unique_id}",
            password="SecurePass123!"
        )

        server_id = str(test_server["server"].id)

        response = test_client.patch(
            f"/api/v1/servers/{server_id}",
            headers={"Authorization": f"Bearer {result.token}"},
            json={"name": "Unauthorized Update"}
        )

        assert response.status_code == 403 or response.status_code == 404


class TestInternalErrors:
    """Tests for internal server error handling."""

    def test_error_does_not_leak_details(self, test_client, auth_headers):
        """Test that internal errors don't leak sensitive details."""
        response = test_client.get(
            "/api/v1/users/999999999999999999",
            headers=auth_headers
        )

        data = response.json()
        assert "traceback" not in str(data).lower()
        assert "stack" not in str(data).lower()
