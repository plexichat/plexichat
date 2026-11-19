"""Regression: every admin license endpoint requires a permission scope.

The license status/feature reads and the license-key validator used to
authenticate with ``get_admin_from_token`` only (session validation). Any admin
holding a valid session -- including a low-privilege read-only role -- could
therefore read the instance id, validation result, expiry, allowed-host list and
feature matrix, and probe arbitrary license payloads through the validator. The
mutation endpoints already required ``admin.license.manage``; the reads now gate
on that same scope so the whole module has one authorization boundary.
"""

import inspect
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.routes.admin import licensing as licensing_routes


_LICENSE_READ_CALLS = (
    ("GET", "/api/v1/admin/license/status", None),
    ("GET", "/api/v1/admin/license/features", None),
    ("POST", "/api/v1/admin/license/validate", {"license_key": "AAAA"}),
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(licensing_routes.router, prefix="/api/v1/admin")
    return TestClient(app)


def test_license_reads_require_a_permission_scope():
    """An authenticated admin without the scope gets 403 on every license read."""
    client = _client()
    seen_scopes = []

    def _deny(request, scope):
        seen_scopes.append(scope)
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    with (
        patch.object(licensing_routes, "check_host_restriction"),
        patch.object(licensing_routes, "require_admin_permission", side_effect=_deny),
    ):
        for method, path, body in _LICENSE_READ_CALLS:
            response = (
                client.post(path, json=body) if method == "POST" else client.get(path)
            )
            assert response.status_code == 403, (path, response.text)

    assert len(seen_scopes) == len(_LICENSE_READ_CALLS)
    assert all(
        scope == licensing_routes._LICENSE_SCOPE for scope in seen_scopes
    ), seen_scopes
    assert licensing_routes._LICENSE_SCOPE == "admin.license.manage"


def test_license_reads_succeed_for_scoped_admin():
    """A caller holding the scope is not blocked by the new gate."""
    client = _client()
    license_module = licensing_routes.licensing_module
    with (
        patch.object(licensing_routes, "check_host_restriction"),
        patch.object(licensing_routes, "require_admin_permission", return_value=1),
        patch.object(license_module, "to_dict", return_value={"features": {}}),
        patch.object(license_module, "get_instance_id", return_value="inst-1"),
        patch.object(license_module, "is_valid", return_value=True),
        patch.object(license_module, "is_free_tier", return_value=True),
        patch.object(license_module, "get_expiry_timestamp", return_value=None),
        patch.object(license_module, "get_validation_result", return_value={}),
        patch.object(license_module, "is_provisional", return_value=False),
        patch.object(license_module, "get_license_type", return_value="free"),
        patch.object(license_module, "get_allowed_hosts", return_value=[]),
    ):
        response = client.get("/api/v1/admin/license/status")
        assert response.status_code == 200, response.text


def test_license_handlers_no_longer_authenticate_only():
    """Structural guard: no license handler may rely on auth-only gating."""
    for handler in (
        licensing_routes.get_license_status,
        licensing_routes.get_license_features,
        licensing_routes.validate_license_key,
        licensing_routes.apply_license,
        licensing_routes.force_validate_license,
    ):
        source = inspect.getsource(handler)
        assert "require_admin_permission(" in source, handler.__name__
        assert "get_admin_from_token(" not in source, handler.__name__
