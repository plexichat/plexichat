"""Tests for version and server-status routes."""


def test_version_endpoint(test_client):
    """Test that version endpoint returns version information."""
    response = test_client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data


def test_version_endpoint_no_auth(test_client):
    """Test that version endpoint works without authentication."""
    response = test_client.get("/api/v1/version")
    assert response.status_code == 200
