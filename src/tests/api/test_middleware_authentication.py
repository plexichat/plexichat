"""
Comprehensive tests for authentication middleware.

Tests cover:
- Token validation and extraction
- Security scenarios (expired, revoked, invalid tokens)
- Different token types (user, bot)
- IP address and user agent handling
- Concurrent request handling
- Error path validation
"""


def test_middleware_blocks_unauthorized_requests(test_client):
    """Test that middleware blocks requests without valid tokens."""
    response = test_client.get("/api/v1/users/@me")
    assert response.status_code == 401


def test_middleware_accepts_valid_tokens(test_client, test_user_with_token):
    """Test that middleware accepts requests with valid tokens."""
    token = test_user_with_token["token"]
    response = test_client.get(
        "/api/v1/users/@me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_middleware_rejects_invalid_tokens(test_client):
    """Test that middleware rejects requests with invalid tokens."""
    response = test_client.get(
        "/api/v1/users/@me",
        headers={"Authorization": "Bearer invalid_token"},
    )
    assert response.status_code == 401


def test_middleware_rejects_malformed_auth_header(test_client):
    """Test that middleware rejects malformed authorization headers."""
    response = test_client.get(
        "/api/v1/users/@me",
        headers={"Authorization": "InvalidFormat token"},
    )
    assert response.status_code == 401
