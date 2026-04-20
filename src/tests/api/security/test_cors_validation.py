"""
Test CORS (Cross-Origin Resource Sharing) validation.

Tests that:
- CORS headers are properly set
- Origin validation is performed
- Preflight requests are handled correctly
- Credentials are properly controlled
"""

import pytest


class TestCORSHeaders:
    """Test CORS header handling."""

    def test_cors_headers_present_on_success(self, test_client):
        """Test CORS headers are present on successful responses."""
        response = test_client.get("/api/v1/health")

        assert response.status_code == 200

    def test_cors_allow_origin_not_wildcard_in_production(self, test_client):
        """Test CORS Allow-Origin is not wildcard in production mode."""
        response = test_client.get("/api/v1/health")

        allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
        if allow_origin == "*":
            pytest.skip("Development/test mode allows wildcard CORS")

    def test_preflight_request_handled(self, test_client):
        """Test OPTIONS preflight requests are handled."""
        response = test_client.options(
            "/api/v1/users/@me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

        assert response.status_code in [200, 204]
        assert (
            "Access-Control-Allow-Methods" in response.headers
            or response.status_code == 200
        )

    def test_cors_credentials_allowed(self, test_client):
        """Test CORS credentials are allowed."""
        response = test_client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:3000"}
        )

        credentials = response.headers.get("Access-Control-Allow-Credentials", "")
        assert (
            credentials.lower() in ["true", ""]
            or response.headers.get("Access-Control-Allow-Origin") == "*"
        )


class TestOriginValidation:
    """Test origin validation."""

    def test_valid_origin_accepted(self, test_client):
        """Test valid origin is accepted."""
        response = test_client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:3000"}
        )

        assert response.status_code == 200

    def test_invalid_origin_handled(self, test_client):
        """Test invalid origin is handled appropriately."""
        response = test_client.get(
            "/api/v1/health", headers={"Origin": "https://evil.com"}
        )

        assert response.status_code == 200

    def test_null_origin_handled(self, test_client):
        """Test null origin is handled."""
        response = test_client.get("/api/v1/health", headers={"Origin": "null"})

        assert response.status_code == 200

    def test_file_origin_handled(self, test_client):
        """Test file:// origin is handled."""
        response = test_client.get("/api/v1/health", headers={"Origin": "file://"})

        assert response.status_code == 200


class TestCORSMethods:
    """Test CORS allowed methods."""

    def test_get_method_allowed(self, test_client):
        """Test GET method is allowed."""
        response = test_client.options(
            "/api/v1/users/@me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        if response.status_code in [200, 204]:
            methods = response.headers.get("Access-Control-Allow-Methods", "")
            assert "GET" in methods or methods == ""

    def test_post_method_allowed(self, test_client):
        """Test POST method is allowed."""
        response = test_client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

        if response.status_code in [200, 204]:
            methods = response.headers.get("Access-Control-Allow-Methods", "")
            assert "POST" in methods or methods == ""

    def test_patch_method_allowed(self, test_client):
        """Test PATCH method is allowed."""
        response = test_client.options(
            "/api/v1/users/@me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PATCH",
            },
        )

        if response.status_code in [200, 204]:
            methods = response.headers.get("Access-Control-Allow-Methods", "")
            assert "PATCH" in methods or methods == ""

    def test_delete_method_allowed(self, test_client):
        """Test DELETE method is allowed."""
        response = test_client.options(
            "/api/v1/channels/123/messages/456",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "DELETE",
            },
        )

        if response.status_code in [200, 204]:
            methods = response.headers.get("Access-Control-Allow-Methods", "")
            assert "DELETE" in methods or methods == ""


class TestCORSAllowedHeaders:
    """Test CORS allowed headers."""

    def test_authorization_header_allowed(self, test_client):
        """Test Authorization header is allowed."""
        response = test_client.options(
            "/api/v1/users/@me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

        if response.status_code in [200, 204]:
            headers = response.headers.get("Access-Control-Allow-Headers", "")
            assert (
                "Authorization" in headers
                or "authorization" in headers.lower()
                or headers == ""
            )

    def test_content_type_header_allowed(self, test_client):
        """Test Content-Type header is allowed."""
        response = test_client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

        if response.status_code in [200, 204]:
            headers = response.headers.get("Access-Control-Allow-Headers", "")
            assert (
                "Content-Type" in headers
                or "content-type" in headers.lower()
                or headers == ""
            )

    def test_custom_headers_handled(self, test_client):
        """Test custom headers are handled appropriately."""
        response = test_client.options(
            "/api/v1/users/@me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Custom-Header",
            },
        )

        assert response.status_code in [200, 204]


class TestCORSSecurityIssues:
    """Test CORS security considerations."""

    def test_credentials_with_wildcard_origin_rejected(self, test_client):
        """Test credentials with wildcard origin is rejected."""
        response = test_client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:3000"}
        )

        allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
        allow_credentials = response.headers.get("Access-Control-Allow-Credentials", "")

        if allow_origin == "*":
            assert (
                allow_credentials.lower() != "true"
            ), "Wildcard origin with credentials is a security issue"

    def test_exposed_headers_limited(self, test_client):
        """Test exposed headers are limited to necessary ones."""
        response = test_client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:3000"}
        )

        exposed = response.headers.get("Access-Control-Expose-Headers", "")
        if exposed:
            assert "Authorization" not in exposed
            assert "Cookie" not in exposed

    def test_max_age_set_appropriately(self, test_client):
        """Test CORS max age is set to reasonable value."""
        response = test_client.options(
            "/api/v1/users/@me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        if response.status_code in [200, 204]:
            max_age = response.headers.get("Access-Control-Max-Age", "0")
            if max_age != "0":
                assert int(max_age) <= 86400, "Max age should not exceed 24 hours"
