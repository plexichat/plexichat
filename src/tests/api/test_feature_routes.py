"""Tests for feature routes."""

import pytest


@pytest.mark.slow
def test_features_endpoint(test_client):
    """Test that the legacy /api/v1/features endpoint does not exist."""
    response = test_client.get("/api/v1/features")
    # No GET /api/v1/features route exists (feature data is served under
    # /api/v1/features/tier-features et al.); the API must answer 404.
    assert response.status_code == 404, response.text


@pytest.mark.slow
def test_features_endpoint_no_auth(test_client):
    """Test that the legacy /api/v1/features endpoint does not exist
    regardless of authentication state."""
    response = test_client.get("/api/v1/features")
    assert response.status_code == 404
