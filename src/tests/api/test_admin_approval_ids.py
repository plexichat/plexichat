"""Admin approval snowflake ids must serialize as strings.

``admin_approvals.id`` is itself a snowflake (``generate_snowflake_id`` in the
request route), as are ``requested_by``/``rejected_by`` (admin ids) and
``target_id`` (the target user's or server's snowflake). Served as JSON numbers
a JS client rounds them above 2^53, so the list/detail responses stringify all
of them. Comment ids, their author admin ids, and the echoed approval id are
stringified for the same reason.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision


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
        "id": BIG,
        "requested_by": BIG + 1,
        "action_type": "server.delete",
        "target_type": "server",
        "target_id": target_id,
        "action_details": None,
        "status": "pending",
        "required_approvals": 2,
        "current_approvals": 0,
        "approved_by": None,
        "rejected_by": BIG + 2,
        "rejection_reason": None,
        "expires_at": None,
        "created_at": 1700000000000,
        "updated_at": 1700000000000,
    }


def test_list_approvals_stringifies_snowflake_ids():
    with _client(_FakeDB([_row(BIG + 3)])) as client:
        response = client.get("/api/v1/admin/approvals")
        assert response.status_code == 200, response.text
        entry = response.json()["approvals"][0]

    assert entry["target_id"] == str(BIG + 3)
    assert isinstance(entry["target_id"], str)
    # The approval's own id and the requested_by/rejected_by admin ids are
    # snowflakes too.
    assert entry["id"] == str(BIG)
    assert isinstance(entry["id"], str)
    assert entry["requested_by"] == str(BIG + 1)
    assert entry["rejected_by"] == str(BIG + 2)
    # Timestamps stay numeric.
    assert entry["created_at"] == 1700000000000


def test_get_approval_stringifies_snowflake_ids():
    with _client(_FakeDB([_row(BIG + 3)])) as client:
        response = client.get(f"/api/v1/admin/approvals/{BIG}")
        assert response.status_code == 200, response.text
        entry = response.json()

    assert entry["target_id"] == str(BIG + 3)
    assert entry["id"] == str(BIG)
    assert entry["requested_by"] == str(BIG + 1)


def test_get_approval_comments_stringifies_snowflake_ids():
    class _CommentsDB:
        def fetch_all(self, query, params=None):
            return [
                {
                    "id": BIG + 1,
                    "approval_id": BIG,
                    "admin_id": BIG + 2,
                    "comment": "looks good",
                    "created_at": 1700000000000,
                }
            ]

        def fetch_one(self, query, params=None):
            return {"id": BIG}

    with _client(_CommentsDB()) as client:
        response = client.get(f"/api/v1/admin/approvals/{BIG}/comments")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["approval_id"] == str(BIG)
    assert isinstance(body["approval_id"], str)
    comment = body["comments"][0]
    assert comment["id"] == str(BIG + 1)
    assert isinstance(comment["id"], str)
    assert comment["admin_id"] == str(BIG + 2)
    assert comment["created_at"] == 1700000000000
