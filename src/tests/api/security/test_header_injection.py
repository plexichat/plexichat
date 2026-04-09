"""
Test header injection and manipulation attempts.

Tests that the API properly handles:
- Malicious headers
- Header injection attempts (CRLF)
- Oversized headers
- Duplicate headers
- Case sensitivity issues
"""

import pytest


class TestHeaderInjection:
    """Test CRLF and header injection attempts."""

    def test_crlf_injection_in_username(self, test_client):
        """Test CRLF injection attempt in username during registration."""
        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": "test\r\nX-Admin: true",
                "email": "test@test.com",
                "password": "TestPass123!",
            },
        )

        assert response.status_code in [400, 422]
        assert "x-admin" not in {k.lower() for k in response.headers.keys()}

    def test_crlf_injection_in_user_agent(self, test_client, create_user_with_token):
        """Test CRLF injection in User-Agent header."""
        user = create_user_with_token()

        response = test_client.get(
            "/api/v1/users/@me",
            headers={
                "Authorization": f"Bearer {user['token']}",
                "User-Agent": "Mozilla/5.0\r\nX-Injected: true",
            },
        )

        assert "x-injected" not in {k.lower() for k in response.headers.keys()}

    def test_newline_injection_in_auth_header(self, test_client):
        """Test newline injection in Authorization header."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer fake_token\nX-Admin: true"},
        )

        assert response.status_code == 401
        assert "x-admin" not in {k.lower() for k in response.headers.keys()}

    def test_header_injection_in_content_type(
        self, test_client, create_user_with_token
    ):
        """Test header injection in Content-Type."""
        user = create_user_with_token()

        response = test_client.patch(
            "/api/v1/users/@me",
            headers={
                "Authorization": f"Bearer {user['token']}",
                "Content-Type": "application/json\r\nX-Evil: header",
            },
            json={"username": "newname"},
        )

        assert "x-evil" not in {k.lower() for k in response.headers.keys()}


class TestMaliciousHeaders:
    """Test handling of malicious header values."""

    def test_extremely_long_header_value(self, test_client):
        """Test handling of extremely long header values."""
        long_value = "A" * 100000

        response = test_client.get("/api/v1/health", headers={"X-Custom": long_value})

        assert response.status_code in [200, 400, 413, 431]

    def test_sql_injection_in_user_agent(self, test_client, create_user_with_token):
        """Test SQL injection attempt in User-Agent."""
        user = create_user_with_token()

        response = test_client.get(
            "/api/v1/users/@me",
            headers={
                "Authorization": f"Bearer {user['token']}",
                "User-Agent": "' OR '1'='1",
            },
        )

        assert response.status_code == 200

    def test_xss_in_custom_header(self, test_client):
        """Test XSS attempt in custom header."""
        response = test_client.get(
            "/api/v1/health", headers={"X-Custom": "<script>alert('xss')</script>"}
        )

        assert response.status_code == 200

    def test_null_byte_in_header(self, test_client):
        """Test null byte in header value."""
        response = test_client.get(
            "/api/v1/health", headers={"X-Custom": "test\x00value"}
        )

        assert response.status_code in [200, 400]

    def test_unicode_in_header(self, test_client):
        """Test unicode characters in header."""
        try:
            response = test_client.get(
                "/api/v1/health", headers={"X-Custom": "test-世界-🌍"}
            )
            assert response.status_code == 200
        except UnicodeEncodeError:
            pytest.skip("Test client does not support unicode headers")


class TestAuthorizationHeaderManipulation:
    """Test manipulation attempts on Authorization header."""

    def test_multiple_bearer_tokens(self, test_client, create_user_with_token):
        """Test multiple bearer tokens in Authorization header."""
        user = create_user_with_token()

        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer {user['token']} Bearer fake_token"},
        )

        assert response.status_code in [200, 401]

    def test_bearer_token_with_semicolon(self, test_client):
        """Test Bearer token with semicolon separator."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer fake_token; admin=true"},
        )

        assert response.status_code == 401

    def test_auth_header_with_encoding_tricks(self, test_client):
        """Test Authorization header with encoding tricks."""
        response = test_client.get(
            "/api/v1/users/@me", headers={"Authorization": "Bearer%20fake_token"}
        )

        assert response.status_code == 401

    def test_case_manipulation_in_bearer(self, test_client, create_user_with_token):
        """Test case manipulation in Bearer keyword."""
        user = create_user_with_token()

        test_cases = ["BEARER", "bearer", "BeArEr", "bEaReR"]

        for case in test_cases:
            response = test_client.get(
                "/api/v1/users/@me",
                headers={"Authorization": f"{case} {user['token']}"},
            )
            assert (
                response.status_code == 401
            ), f"Case variant '{case}' should be rejected"


class TestCustomHeaderValidation:
    """Test validation of custom and optional headers."""

    def test_x_forwarded_for_spoofing(self, test_client):
        """Test X-Forwarded-For header spoofing."""
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "TestPass123!"},
            headers={"X-Forwarded-For": "127.0.0.1, 10.0.0.1"},
        )

        assert response.status_code in [401, 200]

    def test_x_real_ip_spoofing(self, test_client):
        """Test X-Real-IP header spoofing."""
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "TestPass123!"},
            headers={"X-Real-IP": "127.0.0.1"},
        )

        assert response.status_code in [401, 200]

    def test_host_header_injection(self, test_client):
        """Test Host header injection."""
        response = test_client.get("/api/v1/health", headers={"Host": "evil.com"})

        assert response.status_code == 200

    def test_origin_header_validation(self, test_client):
        """Test Origin header is properly validated."""
        response = test_client.options(
            "/api/v1/users/@me", headers={"Origin": "https://evil.com"}
        )

        cors_origin = response.headers.get("Access-Control-Allow-Origin", "")
        assert cors_origin != "https://evil.com" or cors_origin == "*"


class TestContentSecurityHeaders:
    """Test that proper security headers are set."""

    def test_no_sensitive_headers_leaked(self, test_client):
        """Test that sensitive server headers are not leaked."""
        response = test_client.get("/api/v1/health")

        sensitive_headers = ["X-Powered-By", "Server", "X-AspNet-Version"]
        headers_lower = {k.lower(): v for k, v in response.headers.items()}

        for header in sensitive_headers:
            if header.lower() in headers_lower:
                value = headers_lower[header.lower()]
                assert "fastapi" not in value.lower()
                assert "python" not in value.lower()

    def test_error_responses_dont_leak_info(self, test_client):
        """Test error responses don't leak server information."""
        response = test_client.get("/api/v1/nonexistent/route/12345")

        assert response.status_code == 404
        body = response.text.lower()

        assert "traceback" not in body
        assert "stack" not in body
        assert "file" not in body or "not found" in body
