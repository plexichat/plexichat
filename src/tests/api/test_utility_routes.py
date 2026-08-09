"""
Tests for utility routes: health, QR generation, and artifact capabilities.

These endpoints previously had no dedicated route-level coverage:
- /health and /ready probes
- /qr generation (data/size/format validation)
- /capabilities/artifacts (auth-gated capability surface)
"""

import pytest


class TestHealthRoutes:
    """Test the health and readiness probes."""

    def test_readiness_probe(self, test_client):
        """The readiness probe must answer 200 with status ready."""
        response = test_client.get("/api/v1/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_health_check(self, test_client):
        """The health check must report healthy + a version string."""
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert isinstance(data["version"], str)
        assert data["version"]

    def test_health_check_no_methods(self, test_client):
        """POST to /health must be rejected."""
        response = test_client.post("/api/v1/health")
        assert response.status_code == 405


class TestQrRoutes:
    """Test QR code generation."""

    def test_generate_png(self, test_client):
        """A PNG QR code must be returned for valid input."""
        response = test_client.get("/api/v1/qr", params={"data": "https://example.com"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
        assert response.headers.get("cache-control") == "public, max-age=86400"

    def test_generate_jpg(self, test_client):
        """A JPEG QR code must be returned for format=jpg."""
        response = test_client.get(
            "/api/v1/qr", params={"data": "hello", "format": "jpg"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content[:2] == b"\xff\xd8"  # JPEG magic bytes

    def test_generate_custom_size(self, test_client):
        """A custom size must be accepted and produce an image."""
        response = test_client.get(
            "/api/v1/qr", params={"data": "x", "size": "300x150"}
        )
        assert response.status_code == 200
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_empty_data_rejected(self, test_client):
        """Empty data must be rejected with 400."""
        response = test_client.get("/api/v1/qr", params={"data": ""})
        assert response.status_code == 400

    def test_missing_data_rejected(self, test_client):
        """Missing data must be rejected with 422 (validation)."""
        response = test_client.get("/api/v1/qr")
        assert response.status_code in (400, 422)


class TestHelpRoutes:
    """Test the help documentation routes."""

    def test_robots_txt_served(self, test_client):
        """robots.txt must be served under the API prefix."""
        response = test_client.get("/api/v1/robots.txt")
        assert response.status_code == 200
        assert "User-agent" in response.text

    def test_security_logout_help(self, test_client):
        """The security-logout help page must render HTML."""
        response = test_client.get("/api/v1/help/security-logout")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert (
            "<!DOCTYPE html" in response.text.lower()
            or "<html" in response.text.lower()
        )

    def test_access_blocked_help(self, test_client):
        """The access-blocked help page must render HTML."""
        response = test_client.get("/api/v1/help/access-blocked")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


class TestCapabilitiesRoutes:
    """Test the artifact capabilities endpoint."""

    def test_capabilities_requires_auth(self, test_client):
        """The capabilities endpoint must be auth-gated."""
        response = test_client.get("/api/v1/capabilities/artifacts")
        assert response.status_code == 401

    def test_capabilities_returns_features(self, test_client, test_user_with_token):
        """Authenticated callers must get the full capability map."""
        token = test_user_with_token["token"]
        response = test_client.get(
            "/api/v1/capabilities/artifacts",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        for feature in (
            "artifacts",
            "artifacts_whiteboard",
            "voice_transcription",
            "voice_recording",
            "plexiscribe",
            "plexiscript",
            "plexiboard",
            "plexistash",
            "plexisum",
        ):
            assert feature in data
            entry = data[feature]
            assert entry["feature"] == feature
            assert entry["state"] in (
                "available",
                "disabled_by_config",
                "disabled_by_license",
                "dependency_missing",
                "misconfigured",
            )
            assert isinstance(entry["message"], str)

    def test_capabilities_artifacts_state(self, test_client, test_user_with_token):
        """The artifacts gate state must be present and well-formed."""
        token = test_user_with_token["token"]
        response = test_client.get(
            "/api/v1/capabilities/artifacts",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        artifacts = response.json()["artifacts"]
        assert artifacts["state"] in (
            "available",
            "disabled_by_config",
            "misconfigured",
        )
