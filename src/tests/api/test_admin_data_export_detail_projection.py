"""Admin DSAR detail must project only the columns it serializes.

``_get_request_or_404`` used ``SELECT * FROM dsar_requests``, pulling the
internal ``storage_path`` (server filesystem layout), ``checksum`` and
``metadata`` columns into the process on every detail view even though the
response allowlist (``REQUEST_RESPONSE_COLUMNS``) never emits them.  The read
now selects exactly the allowlisted columns, so those internal fields are not
fetched at all.  These tests pin the projection and keep the response shape.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import data_export as data_export_routes

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision

_FULL_ROW = {
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
    # Internal-only columns: must never be fetched by the detail read.
    "storage_path": "/srv/exports/secret",
    "checksum": "deadbeef",
    "metadata": "{}",
}


class _ProjectionDB:
    """Return only the columns named in the SELECT list.

    A ``SELECT *`` is modeled by returning every stored column (the old
    behaviour), so this double exposes the regression directly: the old query
    leaks the internal keys, the projected query does not.
    """

    def __init__(self):
        self.queries = []

    def fetch_one(self, query, params=None):
        self.queries.append(" ".join(query.split()))
        if "SELECT *" in query:
            return dict(_FULL_ROW)
        select = query[
            query.upper().index("SELECT") + len("SELECT") : query.upper().index(" FROM")
        ]
        columns = [c.strip() for c in select.split(",")]
        return {c: _FULL_ROW[c] for c in columns}


def _client():
    app = FastAPI()
    app.include_router(data_export_routes.router, prefix="/api/v1/admin")
    return TestClient(app, raise_server_exceptions=False)


def test_detail_query_projects_only_allowlisted_columns():
    db = _ProjectionDB()
    with (
        patch.object(data_export_routes, "check_host_restriction"),
        patch.object(data_export_routes, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=db),
    ):
        row = data_export_routes._get_request_or_404(BIG)

    assert len(db.queries) == 1
    sql = db.queries[0]
    assert "SELECT *" not in sql
    for internal in ("storage_path", "checksum", "metadata"):
        assert internal not in sql
    assert set(row) == set(data_export_routes.REQUEST_RESPONSE_COLUMNS)


def test_detail_route_response_unchanged_and_internal_fields_absent():
    client = _client()
    with (
        patch.object(data_export_routes, "check_host_restriction"),
        patch.object(data_export_routes, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=_ProjectionDB()),
    ):
        response = client.get(f"/api/v1/admin/data-export/{BIG}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(BIG)
    assert body["user_id"] == str(BIG + 2)
    assert body["admin_id"] == str(BIG + 4)
    assert body["requested_at"] == 1760000000000
    for internal in ("storage_path", "checksum", "metadata"):
        assert internal not in body
