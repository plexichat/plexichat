"""Tests for the custom documentation portal routes."""

import pytest


class TestDocsRoutes:
    """Verify documentation routes render and use runtime-derived URLs."""

    def test_docs_home_uses_runtime_urls(self, test_client):
        response = test_client.get("/docs/api")

        assert response.status_code == 200
        body = response.text
        assert "http://testserver/api/v1" in body
        assert "ws://testserver/gateway" in body
        assert "api.plexichat.com" not in body.lower()
        assert "your-plexichat-host.example" not in body

    @pytest.mark.parametrize(
        "path",
        [
            "/docs/api/features",
            "/docs/api/security",
            "/docs/api/performance",
            "/docs/api/admin-access-tokens",
            "/docs/api/reference/search",
            "/docs/api/reference/notifications",
            "/docs/api/reference/polls",
            "/docs/api/reference/voice",
            "/docs/api/reference/media",
            "/docs/api/reference/reports",
            "/docs/api/reference/feedback",
            "/docs/api/reference/telemetry",
            "/docs/api/reference/system",
            "/docs/api/websocket/opcodes",
            "/docs/api/websocket/close-codes",
        ],
    )
    def test_new_docs_routes_render(self, test_client, path):
        response = test_client.get(path)

        assert response.status_code == 200, path
        assert "PlexiChat Documentation" in response.text

    def test_rate_limits_page_uses_markdown_content(self, test_client):
        response = test_client.get("/docs/api/rate-limits")

        assert response.status_code == 200
        body = response.text
        assert "50 requests per 1 second" in body
        assert "70 requests per 60 seconds" in body
        assert "Rate limits are enforced to ensure stability." not in body

    def test_docs_portal_uses_plexichat_landing_branding(self, test_client):
        response = test_client.get("/docs/api")

        assert response.status_code == 200
        body = response.text
        assert "PLEXI<span>CHAT</span>" in body
        assert "OpenAPI Explorer" in body
        assert "API Reference" in body
        assert "--primary: #6366f1" in body
        assert "--bg: #0b0f19" in body

    def test_swagger_ui_is_branded(self, test_client):
        response = test_client.get("/docs")

        assert response.status_code == 200
        body = response.text
        assert "PlexiChat API Explorer" in body
        assert "Narrative Docs" in body
        assert "ReDoc" in body
        assert "PLEXI<span>CHAT</span>" in body

    def test_redoc_is_branded(self, test_client):
        response = test_client.get("/redoc")

        assert response.status_code == 200
        body = response.text
        assert "PlexiChat API Reference" in body
        assert "OpenAPI Explorer" in body
        assert "Narrative Docs" in body
        assert "PLEXI<span>CHAT</span>" in body

