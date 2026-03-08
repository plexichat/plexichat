"""Tests for the custom documentation portal routes."""

import pytest

import src.api.routes.docs as docs_routes


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
            "/docs/api/permissions",
            "/docs/api/security",
            "/docs/api/performance",
            "/docs/api/admin-access-tokens",
            "/docs/api/oauth-scopes",
            "/docs/api/reference/search",
            "/docs/api/reference/notifications",
            "/docs/api/reference/polls",
            "/docs/api/reference/voice",
            "/docs/api/reference/media",
            "/docs/api/reference/reports",
            "/docs/api/reference/feedback",
            "/docs/api/reference/telemetry",
            "/docs/api/reference/system",
            "/docs/api/websocket/intents",
            "/docs/api/websocket/opcodes",
            "/docs/api/websocket/close-codes",
        ],
    )
    def test_new_docs_routes_render(self, test_client, path):
        response = test_client.get(path)

        assert response.status_code == 200, path
        assert "PlexiChat Documentation" in response.text

    def test_rate_limits_page_uses_live_rate_limit_helper(self, test_client, monkeypatch):
        monkeypatch.setattr(
            docs_routes,
            "get_api_rate_limits",
            lambda: {
                "global": {"requests": 99, "window_seconds": 3, "burst": 7},
                "user": {"requests": 123, "window_seconds": 90, "burst": 11},
                "ip": {"requests": 33, "window_seconds": 15, "burst": 4},
                "bot_multiplier": 2.5,
                "webhook_multiplier": 1.25,
                "admin_bypass": False,
                "internal_bypass": True,
                "routes": {
                    "POST /feedback": {
                        "requests": 8,
                        "window_seconds": 1800,
                        "burst": 1,
                    },
                    "GET /example": {
                        "requests": 13,
                        "window_seconds": 2.5,
                        "burst": 6,
                    },
                },
            },
        )

        response = test_client.get("/docs/api/rate-limits")

        assert response.status_code == 200
        body = response.text
        assert "99 requests per 3 seconds, burst 7" in body
        assert "123 requests per 90 seconds, burst 11" in body
        assert "33 requests per 15 seconds, burst 4" in body
        assert "POST /feedback" in body
        assert "8 requests per 1800 seconds, burst 1" in body
        assert "GET /example" in body
        assert "13 requests per 2.5 seconds, burst 6" in body
        assert "2.5x" in body
        assert "1.25x" in body
        assert "disabled" in body
        assert "enabled" in body

    def test_websocket_intents_page_uses_live_intent_helper(self, test_client, monkeypatch):
        monkeypatch.setattr(
            docs_routes,
            "get_gateway_intents_docs_data",
            lambda: {
                "default_value": 777,
                "all_value": 1023,
                "privileged_value": 96,
                "rows": [
                    {
                        "value": 512,
                        "name": "CUSTOM_INTENT",
                        "default": False,
                        "privileged": True,
                        "description": "Custom dynamic description",
                    }
                ],
            },
        )

        response = test_client.get("/docs/api/websocket/intents")

        assert response.status_code == 200
        body = response.text
        assert "Gateway Intents" in body
        assert "777" in body
        assert "1023" in body
        assert "96" in body
        assert "CUSTOM_INTENT" in body
        assert "Custom dynamic description" in body
        assert "/docs/api/websocket/intents" in body

    def test_permissions_page_uses_live_permissions_helper(self, test_client, monkeypatch):
        monkeypatch.setattr(
            docs_routes,
            "get_permissions_docs_data",
            lambda: {
                "category_count": 2,
                "permission_count": 3,
                "categories": [
                    {
                        "name": "custom",
                        "count": 2,
                        "permissions": ["custom.read", "custom.write"],
                    }
                ],
                "permissions": [
                    {
                        "name": "custom.read",
                        "category": "custom",
                        "default_user": True,
                        "default_bot": False,
                        "bot_restricted": False,
                        "description": "Read custom data",
                    },
                    {
                        "name": "custom.write",
                        "category": "custom",
                        "default_user": False,
                        "default_bot": False,
                        "bot_restricted": True,
                        "description": "Write custom data",
                    },
                ],
            },
        )

        response = test_client.get("/docs/api/permissions")

        assert response.status_code == 200
        body = response.text
        assert "Permissions" in body
        assert "custom.read" in body
        assert "custom.write" in body
        assert "Read custom data" in body
        assert "Write custom data" in body
        assert "/docs/api/permissions" in body

    def test_oauth_scopes_page_uses_live_scope_helper(self, test_client, monkeypatch):
        monkeypatch.setattr(
            docs_routes,
            "get_oauth_scopes_docs_data",
            lambda: {
                "scope_count": 4,
                "privileged_count": 1,
                "bot_required_count": 1,
                "rows": [
                    {
                        "name": "custom.scope",
                        "privileged": True,
                        "bot_required": False,
                        "description": "Custom scope description",
                    },
                    {
                        "name": "bot.install",
                        "privileged": False,
                        "bot_required": True,
                        "description": "Install a bot",
                    },
                ],
            },
        )

        response = test_client.get("/docs/api/oauth-scopes")

        assert response.status_code == 200
        body = response.text
        assert "OAuth Scopes" in body
        assert "custom.scope" in body
        assert "bot.install" in body
        assert "Custom scope description" in body
        assert "Install a bot" in body
        assert "/docs/api/oauth-scopes" in body

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

