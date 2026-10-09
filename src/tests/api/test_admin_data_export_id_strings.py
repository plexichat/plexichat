"""Admin DSAR data-export responses must carry string snowflake ids.

``dsar_requests`` ids are 64-bit snowflakes (``generate_snowflake_id``), and
the admin Route serializes the row through ``_serialize_request``. The
user-facing ``DSARRequestResponse`` already types ``id`` as ``SnowflakeID``
(a string), but the admin list/detail/generate responses returned raw dicts,
so ``id``/``user_id``/``admin_id`` crossed the wire as JSON numbers — a JS
``Number`` rounds them past 2**53 and the admin UI then addresses (and cancels
or approves) the wrong request.

The routes are driven with the module's admin guards patched out so the real
serializer runs; the dsar core calls are stubbed.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision


def _row(**overrides):
    row = {
        "id": BIG,
        "user_id": BIG + 2,
        "status": "pending",
        "requested_at": 1760000000000,
        "completed_at": None,
        "expires_at": None,
        "format": "json",
        "storage_backend": "local",
        "file_size_bytes": None,
        "admin_id": BIG + 4,
        "denial_reason": None,
        "error_message": None,
        # Not in the response allowlist: must never leak.
        "storage_path": "/srv/exports/secret",
        "checksum": "deadbeef",
        "metadata": "{}",
    }
    row.update(overrides)
    return row


def _app():
    from src.api.routes.admin import data_export as data_export_routes

    app = FastAPI()
    app.include_router(data_export_routes.router, prefix="/api/v1/admin")
    return app


def _client():
    return TestClient(_app(), raise_server_exceptions=False)


class _FakeDB:
    def __init__(self, row):
        self._row = row

    def fetch_one(self, query, params=None):
        return self._row


def test_list_data_exports_stringifies_ids():
    from src.api.routes.admin import data_export as data_export_routes

    client = _client()
    with (
        patch.object(data_export_routes, "check_host_restriction"),
        patch.object(data_export_routes, "require_admin_permission", return_value=1),
        patch.object(
            data_export_routes,
            "get_admin_requests",
            return_value=[_row()],
        ),
    ):
        response = client.get("/api/v1/admin/data-export")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["id"] == str(BIG)
    assert isinstance(item["id"], str)
    assert item["user_id"] == str(BIG + 2)
    assert item["admin_id"] == str(BIG + 4)
    # Timestamps stay numeric; internal columns never leak.
    assert item["requested_at"] == 1760000000000
    assert "storage_path" not in item
    assert "checksum" not in item
    assert "metadata" not in item


def test_get_data_export_stringifies_ids():
    from src.api.routes.admin import data_export as data_export_routes

    client = _client()
    with (
        patch.object(data_export_routes, "check_host_restriction"),
        patch.object(data_export_routes, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=_FakeDB(_row())),
    ):
        response = client.get(f"/api/v1/admin/data-export/{BIG}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(BIG)
    assert isinstance(body["id"], str)
    assert body["user_id"] == str(BIG + 2)
    assert body["admin_id"] == str(BIG + 4)
    assert body["requested_at"] == 1760000000000


def test_generate_data_export_stringifies_ids():
    from src.api.routes.admin import data_export as data_export_routes

    client = _client()
    with (
        patch.object(data_export_routes, "check_host_restriction"),
        patch.object(data_export_routes, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=_FakeDB(_row())),
        patch.object(
            data_export_routes, "generate_manual", return_value=_row(status="completed")
        ),
    ):
        response = client.post(f"/api/v1/admin/data-export/{BIG}/generate")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["request"]["id"] == str(BIG)
    assert isinstance(body["request"]["id"], str)
    assert body["request"]["admin_id"] == str(BIG + 4)


def test_host_restriction_is_enforced_on_every_route():
    """Every DSAR data-export route must run the shared host-level guard.

    ``data_export.py`` was the only module under ``api/routes/admin/`` that
    skipped ``check_host_restriction``, so the ``admin_ui.enabled`` gate, the
    proxy-spoofing guard, ``admin_ui.host_restriction.allowed_hosts`` and the
    provisional-licence host allowlist never applied to the endpoints that
    list, approve, deny or generate exports of users' personal data.
    """
    from src.api.routes.admin import data_export as data_export_routes

    host_checked_paths = []

    def _record_host_check(request):
        host_checked_paths.append(request.url.path)

    client = _client()
    with (
        patch.object(
            data_export_routes,
            "check_host_restriction",
            side_effect=_record_host_check,
        ),
        patch.object(data_export_routes, "require_admin_permission", return_value=1),
        patch.object(data_export_routes, "get_admin_requests", return_value=[_row()]),
        patch("src.api.get_db", return_value=_FakeDB(_row())),
        patch.object(data_export_routes, "approve_request"),
        patch.object(data_export_routes, "deny_request"),
        patch.object(
            data_export_routes, "generate_manual", return_value=_row(status="completed")
        ),
    ):
        client.get("/api/v1/admin/data-export")
        client.get(f"/api/v1/admin/data-export/{BIG}")
        client.post(f"/api/v1/admin/data-export/{BIG}/approve")
        client.post(
            f"/api/v1/admin/data-export/{BIG}/deny", json={"reason": "not warranted"}
        )
        client.post(f"/api/v1/admin/data-export/{BIG}/generate")

    assert host_checked_paths == [
        "/api/v1/admin/data-export",
        f"/api/v1/admin/data-export/{BIG}",
        f"/api/v1/admin/data-export/{BIG}/approve",
        f"/api/v1/admin/data-export/{BIG}/deny",
        f"/api/v1/admin/data-export/{BIG}/generate",
    ]
