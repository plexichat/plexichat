"""
Tests for health check endpoint.
"""

import pytest


class TestHealthCheck:
    """Tests for the health check endpoint."""

    def test_health_check_returns_healthy(self, test_client):
        """Test that health check returns healthy status."""
        response = test_client.get("/api/v1/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data

    def test_health_check_returns_version(self, test_client):
        """Test that health check returns version."""
        response = test_client.get("/api/v1/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "1.0.0"

    def test_root_endpoint(self, test_client):
        """Test root endpoint returns API info."""
        response = test_client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data
        assert "api" in data
