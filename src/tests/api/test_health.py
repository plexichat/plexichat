"""Tests for health endpoint."""


def test_health_endpoint(test_client):
    """Test that health endpoint returns 200."""
    response = test_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_health_endpoint_no_auth(test_client):
    """Test that health endpoint works without authentication."""
    response = test_client.get("/api/v1/health")
    assert response.status_code == 200
