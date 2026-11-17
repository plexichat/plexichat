"""Regression tests for the query count of the optimized admin routes.

The approval-write and ticket-write endpoints used to spend an extra read
round trip (or a wide, thrown-away ticket fetch) on their happy path. These
tests count the DB calls each handler makes with the permission/host guards
and the advisory audit/alert side effects stubbed out, so the count reflects
only the handler's own queries.
"""

from contextlib import contextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BIG = 9007199254740993  # past 2^53


class _Cursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount


class _CountingDB:
    """Records `(kind, normalized_query)` for every DB call."""

    def __init__(self, *, fetch_one_result=None, exec_rowcount=1):
        self.calls = []
        self.fetch_one_result = fetch_one_result
        self.exec_rowcount = exec_rowcount

    def fetch_one(self, query, params=None):
        self.calls.append(("one", " ".join(query.split())))
        return self.fetch_one_result

    def fetch_all(self, query, params=None):
        self.calls.append(("all", " ".join(query.split())))
        return []

    def execute(self, query, params=None):
        self.calls.append(("exec", " ".join(query.split())))
        return _Cursor(self.exec_rowcount)

    @property
    def reads(self):
        return [q for kind, q in self.calls if kind in ("one", "all")]

    @property
    def writes(self):
        return [q for kind, q in self.calls if kind == "exec"]


def _approval_row():
    return {
        "id": BIG,
        "requested_by": 2,
        "status": "pending",
        "current_approvals": 0,
        "required_approvals": 1,
        "approved_by": None,
        "expires_at": 4102444800000,  # far future
    }


@contextmanager
def _client(module, db):
    """Mount one admin router with guards and side effects stubbed out."""
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/admin")
    with (
        patch.object(module, "check_host_restriction"),
        patch.object(module, "require_admin_permission", return_value=3),
        patch("src.api.get_db", return_value=db),
        patch("src.core.admin.permissions.log_admin_action", return_value=True),
        patch("src.utils.system_alerts.record_system_alert"),
    ):
        yield TestClient(app)


def test_approve_reads_once_and_writes_once():
    from src.api.routes.admin import approvals as routes

    db = _CountingDB(fetch_one_result=_approval_row())
    with _client(routes, db) as client:
        resp = client.post(f"/api/v1/admin/approvals/{BIG}/approve")
    assert resp.status_code == 200, resp.text

    # One approval read for the CAS vote, one guarded UPDATE -- the former
    # post-UPDATE "SELECT status, current_approvals, required_approvals" is
    # gone.
    assert len(db.reads) == 1, db.reads
    assert len(db.writes) == 1, db.writes
    assert "FROM admin_approvals" in db.reads[0], db.reads


def test_reject_writes_once_without_a_read():
    from src.api.routes.admin import approvals as routes

    db = _CountingDB(exec_rowcount=1)
    with _client(routes, db) as client:
        resp = client.post(
            f"/api/v1/admin/approvals/{BIG}/reject",
            json={"decision": "reject", "reason": "no"},
        )
    assert resp.status_code == 200, resp.text

    # The conditional UPDATE is the only query on the success path.
    assert db.reads == [], db.reads
    assert len(db.writes) == 1, db.writes
    assert "expires_at" in db.writes[0]


def test_comment_writes_once_without_a_read():
    from src.api.routes.admin import approvals as routes

    db = _CountingDB(exec_rowcount=1)
    with _client(routes, db) as client:
        resp = client.post(
            f"/api/v1/admin/approvals/{BIG}/comments", json={"comment": "ok"}
        )
    assert resp.status_code == 200, resp.text

    # The former standalone "SELECT id FROM admin_approvals" existence read
    # is folded into the INSERT ... SELECT.
    assert db.reads == [], db.reads
    assert len(db.writes) == 1, db.writes
    assert "SELECT" in db.writes[0] and "FROM admin_approvals" in db.writes[0]


def test_ticket_status_uses_cheap_existence_probe():
    from src.api.routes.admin import tickets as routes

    db = _CountingDB(fetch_one_result={"1": 1})
    with (
        _client(routes, db) as client,
        patch("src.core.admin.update_ticket_status", return_value=True),
    ):
        resp = client.patch(
            f"/api/v1/admin/tickets/{BIG}/status", json={"status": "open"}
        )
    assert resp.status_code == 200, resp.text

    # A single lightweight existence probe, not the wide feedback/auth_users
    # JOIN that admin.get_ticket used to build a discarded object from.
    assert len(db.reads) == 1, db.reads
    assert db.reads[0] == "SELECT 1 FROM feedback WHERE id = ?", db.reads
