"""Public license endpoints resolve under ``/api/v1`` as well as at root.

Regression test: the web client reads the provisional-license banner state
from ``GET /api/v1/public/license/info``, and the open-routes family test
lists the ``/api/v1/public/license/*`` URLs -- but the router was only
mounted at the app root (``/public/license/*``), so every ``/api/v1``
request 404'd and the banner never displayed. The ``not in (401, 403)``
assertion in the family test cannot catch a 404, hence this explicit file.
"""

from __future__ import annotations


def test_license_info_resolves_under_api_v1(test_client) -> None:
    """The client banner contract: 200 with the licensing state booleans."""
    response = test_client.get("/api/v1/public/license/info")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert "provisional" in body
    assert "free_tier" in body


def test_license_challenge_resolves_under_api_v1(test_client) -> None:
    """The challenge endpoint mints a nonce under ``/api/v1`` too."""
    response = test_client.get("/api/v1/public/license/challenge")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nonce"]
    assert body["ttl_seconds"] > 0


def test_license_export_route_exists_under_api_v1(test_client) -> None:
    """Bare export must fail validation (400), not 404 (missing route).

    The app normalizes request-validation errors to 400; reaching the
    handler at all proves the route is registered under ``/api/v1``.
    """
    response = test_client.get("/api/v1/public/license/export")
    assert response.status_code == 400, response.text


def test_root_license_paths_still_resolve(test_client) -> None:
    """The canonical root mount is preserved for external integrations."""
    response = test_client.get("/public/license/info")
    assert response.status_code == 200, response.text
    assert response.json()["available"] is True

    response = test_client.get("/public/license/challenge")
    assert response.status_code == 200, response.text
    assert response.json()["nonce"]
