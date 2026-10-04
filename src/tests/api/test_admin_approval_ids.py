"""Admin approval ``target_id`` must serialize as a string.

``admin_approvals.target_id`` holds the target user's or server's 64-bit
snowflake for entity-targeting actions. Served as a JSON number a JS client
rounds it above 2^53, so the list/detail responses must stringify it (while
the admin-rowid ``id`` field stays numeric).
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeDB:
    """Fake DB returning fixed approval rows."""

    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, query, params=None):
        return self._rows

    def fetch_one(self, query, params=None):
        return self._rows[0] if self._rows else None


def _client(fake_db):
    from src.api.routes.admin import approvals as approvals_routes

    app = FastAPI()
    app.include_router(approvals_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)

    class _Ctx:
        def __enter__(self):
            self._patches = [
                patch.object(approvals_routes, "check_host_restriction"),
                patch.object(approvals_routes, "require_admin_permission"),
                patch("src.api.get_db", return_value=fake_db),
            ]
            for p in self._patches:
                p.start()
            return client

        def __exit__(self, *exc):
            for p in reversed(self._patches):
                p.stop()
            return False

    return _Ctx()


def _row(target_id):
    return {
        "id": 1,
        "requested_by": 2,
        "action_type": "server.delete",
        "target_type": "server",
        "target_id": target_id,
        "action_details": None,
        "status": "pending",
        "required_approvals": 2,
        "current_approvals": 0,
        "approved_by": None,
        "rejected_by": None,
        "rejection_reason": None,
        "expires_at": None,
        "created_at": 1700000000000,
        "updated_at": 1700000000000,
    }


def test_list_approvals_stringifies_target_id():
    big = 9007199254740993  # past 2^53: a JSON number would lose precision

    with _client(_FakeDB([_row(big)])) as client:
        response = client.get("/api/v1/admin/approvals")
        assert response.status_code == 200, response.text
        entry = response.json()["approvals"][0]

    assert entry["target_id"] == str(big)
    assert isinstance(entry["target_id"], str)
    # Admin-rowid fields stay numeric.
    assert entry["id"] == 1


def test_get_approval_stringifies_target_id():
    big = 9007199254740993

    with _client(_FakeDB([_row(big)])) as client:
        response = client.get("/api/v1/admin/approvals/1")
        assert response.status_code == 200, response.text
        entry = response.json()

    assert entry["target_id"] == str(big)
    assert isinstance(entry["target_id"], str)
