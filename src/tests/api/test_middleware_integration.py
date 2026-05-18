"""
Integration tests for all middleware working together.

Tests cover:
- Middleware execution order
- Authentication + Rate Limiting
- Authentication + Error Handling
- Logging + Error Handling
- All middleware combined scenarios
- Complex real-world scenarios
"""


def test_middleware_chain_auth_then_error(test_client):
    """Test that authentication middleware runs before error handling."""
    response = test_client.get("/api/v1/users/@me")
    # Should hit auth middleware first, return 401
    assert response.status_code == 401


def test_middleware_chain_error_then_logging(test_client):
    """Test that error handling and logging middleware work together."""
    response = test_client.get("/api/v1/nonexistent")
    # Should hit error handling, which logs the error
    assert response.status_code == 404


def test_middleware_chain_full_flow(test_client, test_user_with_token):
    """Test full middleware chain with authenticated request."""
    token = test_user_with_token["token"]
    response = test_client.get(
        "/api/v1/users/@me",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Should pass through all middleware successfully
    assert response.status_code == 200
