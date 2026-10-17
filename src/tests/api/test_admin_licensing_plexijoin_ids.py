"""HTTP tests for admin licensing / PlexiJoin id serialization.

``GET /admin/plexijoin/connections`` and ``/connections/{id}`` returned
``created_by`` (the creating admin's snowflake) as a raw integer, and
``GET /admin/license/status`` is pinned to serve its ``instance_id`` as a
string. Connection/request ``id`` values are ``INTEGER PRIMARY KEY`` rowids, so
those stay numeric.

Device-trust already has an end-to-end route test in
``src/tests/admin/test_device_trust_overview.py``.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision


class TestPlexiJoinIdContract:
    def _client(self, manager):
        from src.api.routes.admin import plexijoin as plexijoin_routes

        app = FastAPI()
        app.include_router(plexijoin_routes.router, prefix="/api/v1/admin")
        patchers = (
            patch.object(plexijoin_routes, "check_host_restriction"),
            patch.object(plexijoin_routes, "get_admin_from_token", return_value=1),
            patch.object(
                plexijoin_routes, "_get_plexijoin_manager", return_value=manager
            ),
        )
        for p in patchers:
            p.start()
        return TestClient(app), patchers

    def test_connections_list_stringifies_created_by(self):
        connection = {
            "id": 7,  # INTEGER PRIMARY KEY rowid
            "remote_instance_id": "remote:example",
            "remote_url": "https://remote.example",
            "status": "active",
            "connected_at": 1,
            "messages_in": 0,
            "messages_out": 0,
            "last_activity": 2,
            "note": None,
            "created_at": 1,
            "created_by": BIG,
        }

        class _Manager:
            def list_connections(self, status=None, page=1, per_page=50):
                return {
                    "connections": [connection],
                    "total": 1,
                    "page": 1,
                    "per_page": 50,
                    "pages": 1,
                }

            def get_connection(self, connection_id):
                return connection

        client, patchers = self._client(_Manager())
        try:
            listed = client.get("/api/v1/admin/plexijoin/connections")
            assert listed.status_code == 200, listed.text
            row = listed.json()["connections"][0]
            assert isinstance(row["created_by"], str)
            assert row["created_by"] == str(BIG)
            # Connection id is a rowid and stays numeric.
            assert row["id"] == 7

            detail = client.get("/api/v1/admin/plexijoin/connections/7")
            assert detail.status_code == 200, detail.text
            body = detail.json()
            assert isinstance(body["created_by"], str)
            assert body["created_by"] == str(BIG)
            assert body["id"] == 7
        finally:
            for p in reversed(patchers):
                p.stop()

    def test_requests_stringify_reviewed_by(self):
        request_row = {
            "id": 3,  # INTEGER PRIMARY KEY rowid
            "remote_instance_id": "remote:example",
            "remote_url": "https://remote.example",
            "requested_by": "remote-admin",
            "note": None,
            "status": "approved",
            "requested_at": 1,
            "reviewed_at": 2,
            "reviewed_by": BIG,
        }

        class _Manager:
            def list_requests(self, status=None, page=1, per_page=50):
                return {
                    "requests": [request_row],
                    "total": 1,
                    "page": 1,
                    "per_page": 50,
                    "pages": 1,
                }

        client, patchers = self._client(_Manager())
        try:
            resp = client.get("/api/v1/admin/plexijoin/requests")
            assert resp.status_code == 200, resp.text
            row = resp.json()["requests"][0]
            assert isinstance(row["reviewed_by"], str)
            assert row["reviewed_by"] == str(BIG)
            assert row["id"] == 3
        finally:
            for p in reversed(patchers):
                p.stop()


class TestLicenseStatusIdContract:
    def test_status_serves_string_instance_id(self):
        from src.api.routes.admin import licensing as licensing_routes

        app = FastAPI()
        app.include_router(licensing_routes.router, prefix="/api/v1/admin")
        patchers = (
            patch.object(licensing_routes, "check_host_restriction"),
            patch.object(licensing_routes, "get_admin_from_token", return_value=1),
            patch.object(
                licensing_routes.licensing_module,
                "to_dict",
                return_value={"features": {}},
            ),
            patch.object(
                licensing_routes.licensing_module,
                "get_instance_id",
                return_value=str(BIG),
            ),
            patch.object(
                licensing_routes.licensing_module, "is_valid", return_value=True
            ),
            patch.object(
                licensing_routes.licensing_module, "is_free_tier", return_value=True
            ),
            patch.object(
                licensing_routes.licensing_module,
                "get_expiry_timestamp",
                return_value=None,
            ),
            patch.object(
                licensing_routes.licensing_module,
                "get_validation_result",
                return_value={},
            ),
            patch.object(
                licensing_routes.licensing_module, "is_provisional", return_value=False
            ),
            patch.object(
                licensing_routes.licensing_module,
                "get_license_type",
                return_value="free",
            ),
            patch.object(
                licensing_routes.licensing_module, "get_allowed_hosts", return_value=[]
            ),
        )
        for p in patchers:
            p.start()
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/admin/license/status")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["instance_id"] == str(BIG)
            assert isinstance(body["instance_id"], str)
            # Booleans stay booleans.
            assert body["valid"] is True
            assert body["free_tier"] is True
        finally:
            for p in reversed(patchers):
                p.stop()
