"""Admin JSON endpoints must serialize snowflake ids as decimal strings.

The sweep covers three places where a 64-bit snowflake leaked as a JSON number
(a JS Number rounds it above 2^53, so lookups made from the admin UI miss):

* ``GET /admin/device-trust/overview`` — the aggregation helper already
  stringifies user/device/session ids, but the Pydantic response schema
  declared them ``int`` and coerced them straight back to numbers.
* ``GET /admin/roles`` / ``GET /admin/roles/{id}`` /
  ``GET /admin/admins/{id}/roles`` — ``admin_roles.id`` and the admin
  ``created_by``/``assigned_by`` ids are snowflakes (``generate_snowflake_id``).
* ``GET /admin/security/blocked-ips`` — ``blocked_by`` is the blocking admin's
  snowflake.
"""

import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision


class TestDeviceTrustSchemaKeepsIdsAsStrings:
    """The schema must not coerce the helper's stringified snowflakes to int."""

    def test_overview_preserves_string_ids(self):
        from src.api.schemas.admin import DeviceTrustOverviewResponse

        payload = {
            "policy": {
                "expire_hours": 168,
                "trusted_expire_hours": 720,
                "trusted_override_enabled": True,
                "effective_trusted_expire_hours": 720,
                "extend_on_activity": True,
                "extend_threshold_hours": 24,
                "trusted_extend_threshold_hours": 168,
            },
            "users": [
                {
                    "user_id": str(BIG),
                    "devices": [
                        {
                            "id": str(BIG + 1),
                            "name": "laptop",
                            "device_type": "desktop",
                            "trusted": True,
                            "trusted_at": 1,
                            "first_seen_at": 1,
                            "last_seen_at": 2,
                        }
                    ],
                    "trusted_device_count": 1,
                    "sessions": [
                        {
                            "id": str(BIG + 2),
                            "device_id": str(BIG + 1),
                            "device_trusted": True,
                            "duration_hours": 720,
                            "policy_kind": "trusted",
                            "created_at": 1,
                            "expires_at": 2,
                            "last_activity": 2,
                            "expired": False,
                        }
                    ],
                    "session_count": 1,
                    "active_session_count": 1,
                }
            ],
            "totals": {
                "users": 1,
                "devices": 1,
                "trusted_devices": 1,
                "sessions": 1,
                "active_sessions": 1,
                "trusted_device_sessions": 1,
            },
        }

        dumped = json.loads(DeviceTrustOverviewResponse(**payload).model_dump_json())
        entry = dumped["users"][0]
        assert entry["user_id"] == str(BIG)
        assert isinstance(entry["user_id"], str)
        assert entry["devices"][0]["id"] == str(BIG + 1)
        assert isinstance(entry["devices"][0]["id"], str)
        assert entry["sessions"][0]["id"] == str(BIG + 2)
        assert entry["sessions"][0]["device_id"] == str(BIG + 1)
        # Timestamps and counts stay numeric.
        assert entry["devices"][0]["last_seen_at"] == 2
        assert dumped["totals"]["users"] == 1


def _start_patches(module, fake_db):
    patchers = (
        patch.object(module, "check_host_restriction"),
        patch.object(module, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=fake_db),
    )
    for p in patchers:
        p.start()
    return patchers


def _stop(patchers):
    for p in reversed(patchers):
        p.stop()


class _ListDB:
    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, query, params=None):
        return self._rows

    def fetch_one(self, query, params=None):
        return self._rows[0] if self._rows else None


def test_list_roles_stringifies_role_and_admin_ids():
    from src.api.routes.admin import roles as roles_routes

    row = {
        "id": BIG,
        "name": "support_admin",
        "description": "d",
        "permissions": "{}",
        "created_at": 1,
        "created_by": BIG + 1,
        "updated_at": None,
        "is_system": 0,
    }
    app = FastAPI()
    app.include_router(roles_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = _start_patches(roles_routes, _ListDB([row]))
    try:
        response = client.get("/api/v1/admin/roles")
        assert response.status_code == 200
        role = response.json()["roles"][0]
        assert role["id"] == str(BIG)
        assert isinstance(role["id"], str)
        assert role["created_by"] == str(BIG + 1)
        assert isinstance(role["created_by"], str)
        # Timestamps stay numeric.
        assert role["created_at"] == 1
    finally:
        _stop(patchers)


def test_get_role_stringifies_ids_from_tuple_rows():
    from src.api.routes.admin import roles as roles_routes

    row = (BIG, "support_admin", "d", "{}", 1, BIG + 1, None, 0)
    app = FastAPI()
    app.include_router(roles_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = _start_patches(roles_routes, _ListDB([row]))
    try:
        response = client.get(f"/api/v1/admin/roles/{BIG}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(BIG)
        assert body["created_by"] == str(BIG + 1)
    finally:
        _stop(patchers)


def test_admin_roles_stringifies_admin_and_assignment_ids():
    from src.api.routes.admin import roles as roles_routes

    row = (BIG, "support_admin", "d", "{}", 0, 1, BIG + 2)
    app = FastAPI()
    app.include_router(roles_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = _start_patches(roles_routes, _ListDB([row]))
    try:
        response = client.get(f"/api/v1/admin/admins/{BIG + 3}/roles")
        assert response.status_code == 200
        body = response.json()
        assert body["admin_id"] == str(BIG + 3)
        assert isinstance(body["admin_id"], str)
        assert body["roles"][0]["id"] == str(BIG)
        assert body["roles"][0]["assigned_by"] == str(BIG + 2)
    finally:
        _stop(patchers)


def test_blocked_ips_stringifies_blocked_by():
    from src.api.routes.admin import security as security_routes

    fake_ips = [
        {
            "ip_address": "1.2.3.4",
            "reason": "abuse",
            "blocked_at": 1,
            "blocked_by": BIG,
            "expires_at": None,
            "ip_index": "x",
        }
    ]
    app = FastAPI()
    app.include_router(security_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = (
        patch.object(security_routes, "check_host_restriction"),
        patch.object(security_routes, "require_admin_permission", return_value=1),
        patch("src.core.auth.get_blocked_ips", return_value=fake_ips),
    )
    for p in patchers:
        p.start()
    try:
        response = client.get("/api/v1/admin/security/blocked-ips")
        assert response.status_code == 200
        blocked = response.json()[0]
        assert blocked["blocked_by"] == str(BIG)
        assert isinstance(blocked["blocked_by"], str)
        assert blocked["blocked_at"] == 1
    finally:
        _stop(patchers)


def test_cross_worker_tuning_audit_stringifies_admin_id():
    """The acting admin id in the tuning audit trail is a snowflake."""
    from src.api.routes.admin import dashboard as dashboard_routes

    class _AuditDB:
        def fetch_all(self, query, params=None):
            return [
                {
                    "id": 7,
                    "admin_id": BIG,
                    "username": None,
                    "details": "{}",
                    "ip_address": "1.2.3.4",
                    "status": "success",
                    "created_at": 1700000000000,
                }
            ]

    app = FastAPI()
    app.include_router(dashboard_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = (
        patch.object(dashboard_routes, "check_host_restriction"),
        patch.object(dashboard_routes, "get_admin_from_token", return_value=1),
        patch("src.api.get_db", return_value=_AuditDB()),
    )
    for p in patchers:
        p.start()
    try:
        response = client.get("/api/v1/admin/dashboard/cross-worker-tuning/audit")
        assert response.status_code == 200
        entry = response.json()["entries"][0]
        assert entry["admin_id"] == str(BIG)
        assert isinstance(entry["admin_id"], str)
        # Audit row id (rowid) stays numeric.
        assert entry["id"] == 7
    finally:
        _stop(patchers)


def test_plexijoin_listed_reviewed_by_is_string():
    """``reviewed_by`` is the reviewing admin's snowflake."""
    from src.api.routes.admin import plexijoin as plexijoin_routes

    class _Manager:
        def list_requests(self, status=None, page=1, per_page=50):
            return {
                "requests": [
                    {
                        "id": 5,
                        "remote_instance_id": "remote-1",
                        "remote_url": "https://remote",
                        "requested_by": "remote-1",
                        "note": None,
                        "status": "approved",
                        "requested_at": 1,
                        "reviewed_at": 2,
                        "reviewed_by": BIG,
                    }
                ],
                "total": 1,
                "page": 1,
                "per_page": 50,
                "pages": 1,
            }

    app = FastAPI()
    app.include_router(plexijoin_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = (
        patch.object(plexijoin_routes, "check_host_restriction"),
        patch.object(plexijoin_routes, "get_admin_from_token", return_value=1),
        patch.object(
            plexijoin_routes, "_get_plexijoin_manager", lambda request: _Manager()
        ),
    )
    for p in patchers:
        p.start()
    try:
        response = client.get("/api/v1/admin/plexijoin/requests")
        assert response.status_code == 200, response.text
        req = response.json()["requests"][0]
        assert req["reviewed_by"] == str(BIG)
        assert isinstance(req["reviewed_by"], str)
        # Rowid ids stay numeric.
        assert req["id"] == 5
    finally:
        _stop(patchers)
