"""Full report detail requires the ``reports.read`` scope for admins.

``GET /features/reports/{id}`` used to grant full admin visibility -- including
the reporter's identity and the plaintext preview of the reported message --
to any account present in ``admin_users``. Admin accounts are scoped by role:
a tickets-only support admin has no business reading a message report from a
server they are not a member of, and the sibling admin report listings all
require ``reports.read``. Membership alone must not bypass that scope.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.features.reports import get_report_details
from src.api.schemas.common import SnowflakeID
from src.core.admin.request_roles import clear_session_roles

pytestmark = pytest.mark.unit

REPORT_ID = 7
REPORTER_ID = "555"
ADMIN_ID = "900"


def _viewer(user_id: str) -> Any:
    return SimpleNamespace(user_id=SnowflakeID(user_id), token_type="user")


class _ScopeDB:
    """DB double exposing admin membership and per-admin role scopes."""

    def __init__(self, *, admin_ids=(), role_permissions=None):
        self.admin_ids = {int(value) for value in admin_ids}
        self.role_permissions = role_permissions or {}
        self.calls: list[str] = []

    def fetch_one(self, sql, params=()):
        self.calls.append(sql)
        if "FROM reports" in sql:
            # Shape matches a BIGINT row as returned by drivers that stringify
            # snowflakes; the exact types are irrelevant to the admin gate.
            return {
                "id": REPORT_ID,
                "reporter_id": REPORTER_ID,
                "report_type": "message",
                "target_id": "42",
                "server_id": "999",
                "message_content": "secret message from a foreign server",
                "reason": "spam",
                "category": "spam",
                "details": None,
                "status": "open",
                "priority": "high",
                "admin_notes": "internal only",
                "reviewed_by": None,
                "reviewed_at": None,
                "resolved_by": None,
                "resolved_at": None,
                "action_taken": None,
                "resolution": None,
                "escalated_at": None,
                "assigned_to": None,
                "updated_at": None,
            }
        if "FROM admin_users" in sql:
            uid = int(cast(Any, params)[0])
            return {"id": uid} if uid in self.admin_ids else None
        return None

    def fetch_all(self, sql, params=()):
        self.calls.append(sql)
        if "admin_roles" in sql and "admin_role_assignments" in sql:
            uid = int(cast(Any, params)[0])
            return [
                {
                    "id": idx + 1,
                    "name": f"role-{idx}",
                    "description": "",
                    "permissions": json.dumps(payload),
                    "created_at": 0,
                    "created_by": 0,
                    "is_system": 0,
                }
                for idx, payload in enumerate(self.role_permissions.get(uid, []))
            ]
        return []


def _call(db: _ScopeDB, viewer_id: str):
    return asyncio.run(get_report_details(REPORT_ID, _viewer(viewer_id)))


@pytest.fixture(autouse=True)
def _wire_db(monkeypatch):
    """Bind the module under test to the DB double installed by each test."""
    clear_session_roles()
    holder: dict[str, _ScopeDB] = {}
    monkeypatch.setattr(api, "get_db", lambda: holder["db"])
    monkeypatch.setattr(api, "get_reports", lambda: None)
    return holder


def _install(holder, db):
    holder["db"] = db
    return db


def _as_admin_without_scope():
    return _ScopeDB(
        admin_ids=[ADMIN_ID],
        role_permissions={int(ADMIN_ID): [{"tickets.read": True}]},
    )


def _as_admin_with_scope():
    return _ScopeDB(
        admin_ids=[ADMIN_ID],
        role_permissions={int(ADMIN_ID): [{"reports.read": True}]},
    )


def test_admin_without_reports_read_scope_is_denied(_wire_db):
    """Regression: bare admin_users membership must not expose report details."""
    db = _install(_wire_db, _as_admin_without_scope())

    with pytest.raises(HTTPException) as exc:
        _call(db, ADMIN_ID)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_admin_with_reports_read_scope_sees_full_report(_wire_db):
    db = _install(_wire_db, _as_admin_with_scope())

    result = _call(db, ADMIN_ID)["report"]

    assert result["admin_notes"] == "internal only"
    assert result["reporter_id"] == REPORTER_ID
    assert result["message_content"] == "secret message from a foreign server"


def test_non_admin_reporter_still_reads_own_report(_wire_db):
    """The reporter path must keep working and stay stripped of admin fields."""
    db = _install(_wire_db, _ScopeDB(admin_ids=[]))

    result = _call(db, REPORTER_ID)["report"]

    assert result["message_content"] == "secret message from a foreign server"
    assert "admin_notes" not in result
    assert "resolution" not in result
    assert "reviewed_by" not in result


def test_unrelated_non_admin_is_denied(_wire_db):
    db = _install(_wire_db, _ScopeDB(admin_ids=[]))

    with pytest.raises(HTTPException) as exc:
        _call(db, "123456")

    assert exc.value.status_code == 403
