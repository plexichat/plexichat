"""
Tests for health check endpoint.
"""


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
        """Test that health check returns version in correct format."""
        response = test_client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        # Version should be in format [a|b|c|r].[major].[minor]-[build] or "unknown"
        version = data["version"]
        assert version == "unknown" or (
            version[0] in "abcr" and "." in version and "-" in version
        )

    def test_root_endpoint(self, test_client):
        """Test root endpoint returns API info."""
        response = test_client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data
        assert "api" in data
