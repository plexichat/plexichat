"""SEC100 - end-to-end invariant: admin routes reject anonymous callers.

The in-process FastAPI ``TestClient`` is used throughout.  Two properties are
pinned down:

* Behavioural: a representative set of permission-gated admin GET routes must
  answer an unauthenticated request with 401/403 (never a 2xx and never a 5xx).
  The admin UI host gate is neutralised for these requests so the assertion
  exercises the *session/permission* gate rather than the "UI disabled" 404.

* Structural: the admin route surface advertised by the app's OpenAPI schema
  must be non-trivial and must contain every route that the behavioural test
  covers, so the sample cannot silently drift away from the real route table.

No timing-based assertions; every check is a deterministic status-code or
set-membership comparison.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import utils.config as config

# Read-only, permission-gated admin GET routes with no required query/path
# parameters.  Each is expected to fail closed at the admin session gate.
GATED_ADMIN_GET_ROUTES = [
    "/api/v1/admin/audit/logs",
    "/api/v1/admin/audit/logs/stats",
    "/api/v1/admin/audit/logs/export",
    "/api/v1/admin/dashboard",
    "/api/v1/admin/dashboard/system-alerts",
    "/api/v1/admin/security/blocked-ips",
    "/api/v1/admin/security/banned-usernames",
    "/api/v1/admin/device-trust/overview",
    "/api/v1/admin/roles",
    "/api/v1/admin/approvals",
    "/api/v1/admin/bots/stats",
    "/api/v1/admin/bots/applications",
    "/api/v1/admin/telemetry/stats",
    "/api/v1/admin/database/pool-health",
    "/api/v1/admin/database/migrations/status",
]


# Capture the genuine getter once, before any test patches it.
_ORIGINAL_GET = config.get


@pytest.fixture
def admin_ui_enabled():
    """Temporarily present an admin-UI-enabled configuration to the app."""

    def _get(key, default=None):
        if key == "admin_ui":
            return {"enabled": True, "host_restriction": {"enabled": False}}
        return _ORIGINAL_GET(key, default)

    with patch.object(config, "get", side_effect=_get):
        yield


def _admin_paths(test_client) -> set[str]:
    schema = test_client.app.openapi()
    return {p for p in schema.get("paths", {}) if p.startswith("/api/v1/admin")}


@pytest.mark.parametrize("path", GATED_ADMIN_GET_ROUTES)
def test_gated_admin_route_denies_anonymous_caller(test_client, admin_ui_enabled, path):
    response = test_client.get(path)
    assert response.status_code in (401, 403), (
        f"anonymous GET {path} returned {response.status_code}: {response.text}"
    )


def test_admin_route_surface_is_non_vacuous(test_client):
    paths = _admin_paths(test_client)
    assert len(paths) >= 100, f"unexpectedly small admin route table: {len(paths)}"


def test_covered_routes_are_part_of_the_admin_surface(test_client):
    paths = _admin_paths(test_client)
    missing = set(GATED_ADMIN_GET_ROUTES) - paths
    assert not missing, f"behavioural sample not in the admin route table: {missing}"
