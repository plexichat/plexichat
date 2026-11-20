"""Regression: admin feature routes must enforce the admin host perimeter.

The four ``/admin/*`` feature routes (user tier/feature edits, tier catalogue
and badge catalogue) are authenticated through ``get_admin_access``. That
dependency used to trust a valid admin credential alone, so the host allowlist
(and the provisional-licence host gate) that every other ``/admin/*`` surface
enforces was skipped entirely here. A leaked admin token could therefore be
replayed from any network peer to change user tiers and feature settings.

These tests pin that the dependency runs ``check_host_restriction`` before it
accepts either an admin user token or a dedicated admin session.
"""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.routes.features.tier_features import get_admin_access
import src.api.routes.admin.utils as admin_utils


class _Admin:
    permissions = {"administrator": True}


def _request(client_host: str) -> Request:
    """Build a request carrying a valid admin user in its scope state."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/tiers",
            "headers": [(b"host", b"testserver")],
            "client": (client_host, 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
            "state": {"user": _Admin()},
        }
    )


@pytest.fixture(autouse=True)
def _host_config(monkeypatch):
    """Allow only loopback peers for the admin surface during these tests."""

    def fake_get(key, default=None):
        if key == "admin_ui":
            return {
                "enabled": False,
                "host_restriction": {
                    "enabled": True,
                    "allowed_hosts": ["127.0.0.1", "localhost", "::1"],
                },
            }
        if key == "api":
            return {"trusted_proxies": []}
        return default

    monkeypatch.setattr(admin_utils.config, "get", fake_get)
    # The provisional-licence check is a separate gate; keep it out of scope
    # so these tests pin only the host-allowlist behaviour.
    from src.utils import licensing

    monkeypatch.setattr(licensing, "is_provisional", lambda: False)


def test_remote_admin_credential_is_denied():
    """A valid admin credential from a non-allowlisted host must be rejected."""
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_admin_access(_request("203.0.113.10")))
    assert exc_info.value.status_code == 403


def test_loopback_admin_credential_is_accepted():
    """An admin credential from an allowlisted host still works."""
    result = asyncio.run(get_admin_access(_request("127.0.0.1")))
    assert result is not None
    assert result.permissions.get("administrator") is True


def test_host_gate_runs_before_session_fallback(monkeypatch):
    """Even the dedicated-admin-session path cannot bypass the host gate."""
    called = []

    def fake_admin_from_token(request):
        called.append(request)
        return 1

    monkeypatch.setattr(admin_utils, "get_admin_from_token", fake_admin_from_token)
    # tier_features imported the helper by value, so patch that binding too.
    import src.api.routes.features.tier_features as tier_features

    monkeypatch.setattr(tier_features, "get_admin_from_token", fake_admin_from_token)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_admin_access(_request("203.0.113.10")))
    assert exc_info.value.status_code == 403
    assert called == []  # session validation must not even be attempted
