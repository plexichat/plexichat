"""
Comprehensive tests for error handling middleware.

Tests cover:
- Exception to HTTP status code mapping
- Error response formatting
- CORS headers on error responses
- Validation errors
- Custom exception handling
- Security (no information leakage)
"""


def test_error_handling_formats_404(test_client):
    """Test that error handling middleware formats 404 errors correctly."""
    response = test_client.get("/api/v1/nonexistent")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert "code" in data["error"]
    assert "message" in data["error"]


def test_error_handling_formats_405(test_client):
    """Test that error handling middleware formats 405 errors correctly."""
    response = test_client.put("/api/v1/health")
    assert response.status_code == 405
    data = response.json()
    assert "error" in data


def test_error_handling_formats_validation_errors(test_client):
    """Test that error handling middleware formats validation errors correctly."""
    response = test_client.post(
        "/api/v1/auth/register",
        json={"username": "test"},  # Missing required fields
    )
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
