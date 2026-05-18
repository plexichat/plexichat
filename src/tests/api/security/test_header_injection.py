"""Tests for header injection attacks."""


def test_xss_in_user_agent(test_client):
    """Test that XSS in User-Agent header is sanitized."""
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": "wrong"},
        headers={"User-Agent": "<script>alert('xss')</script>"},
    )

    # Should fail authentication but not crash
    assert response.status_code in [401, 400]


def test_header_injection_in_authorization(test_client):
    """Test that header injection in Authorization is handled safely."""
    response = test_client.get(
        "/api/v1/users/@me",
        headers={"Authorization": "Bearer \r\nX-Injected: true"},
    )

    # Should fail authentication but not crash
    assert response.status_code == 401


def test_crlf_injection_in_headers(test_client):
    """Test that CRLF injection in headers is prevented."""
    response = test_client.get(
        "/api/v1/",
        headers={"X-Custom": "value\r\nX-Injected: malicious"},
    )

    # Should handle the request safely
    assert response.status_code == 200


def test_long_header_value(test_client):
    """Test that excessively long header values are handled."""
    long_value = "A" * 10000
    response = test_client.get(
        "/api/v1/",
        headers={"X-Long-Header": long_value},
    )

    # Should either reject or handle safely
    assert response.status_code in [200, 400, 413, 431]
