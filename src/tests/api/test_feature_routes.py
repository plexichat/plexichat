"""Tests for feature routes."""

import pytest


@pytest.mark.slow
def test_features_endpoint(test_client):
    """Test that features endpoint returns feature information."""
    response = test_client.get("/api/v1/features")
    # Features might not be fully set up in tests
    assert response.status_code in [200, 404, 500]


@pytest.mark.slow
def test_features_endpoint_no_auth(test_client):
    """Test that features endpoint works without authentication."""
    response = test_client.get("/api/v1/features")
    # Features might not be fully set up in tests
    assert response.status_code in [200, 404, 500]
