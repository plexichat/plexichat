"""Report-status mutations require the ``reports.action`` admin scope.

``PATCH /reports/{id}/status`` used to authorize a moderation state change by
bare membership in ``admin_users``. Admin accounts are scoped by role: every
sibling admin report action requires ``reports.action``, and
``get_report_details`` already requires ``reports.read`` for admins. Without
the scope check, a tickets-only support admin could change any report's
status, priority, assignment, resolution and internal notes.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.features.reports import (
    ReportStatusUpdateRequest,
    update_report_status,
)
from src.api.schemas.common import SnowflakeID
from src.core.admin.request_roles import clear_session_roles

pytestmark = pytest.mark.unit

REPORT_ID = 7
ADMIN_ID = "900"


def _admin() -> Any:
    return SimpleNamespace(user_id=SnowflakeID(ADMIN_ID), token_type="user")


class _ScopeDB:
    """DB double: admin membership plus per-admin role scope payloads."""

    def __init__(self, *, admin_ids=(), role_permissions=None):
        self.admin_ids = {int(value) for value in admin_ids}
        self.role_permissions = role_permissions or {}
        self.updates: list[str] = []

    def fetch_one(self, sql, params=()):
        # The gate query resolves is_admin and report_exists in one row.
        if "AS is_admin" in sql and "AS report_exists" in sql:
            uid = int(cast(Any, params)[0])
            return {
                "is_admin": 1 if uid in self.admin_ids else None,
                "report_exists": 1,
            }
        return None

    def fetch_all(self, sql, params=()):
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

    def execute(self, sql, params=()):
        self.updates.append(sql)


@pytest.fixture(autouse=True)
def _wire_db(monkeypatch):
    holder: dict[str, _ScopeDB] = {}
    monkeypatch.setattr(api, "get_db", lambda: holder["db"])
    clear_session_roles()
    return holder


def _call(db: _ScopeDB, status="resolved"):
    return asyncio.run(
        update_report_status(
            REPORT_ID,
            ReportStatusUpdateRequest(status=status),
            _admin(),
        )
    )


def _admin_db(scopes):
    return _ScopeDB(
        admin_ids=[ADMIN_ID],
        role_permissions={int(ADMIN_ID): [scopes]},
    )


def _install(holder, db):
    holder["db"] = db
    return db


def test_admin_without_reports_action_scope_is_denied(_wire_db):
    """Regression: admin membership alone must not permit a status write."""
    db = _install(_wire_db, _admin_db({"tickets.read": True}))

    with pytest.raises(HTTPException) as exc:
        _call(db)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    assert db.updates == []


def test_admin_with_reports_action_scope_can_update(_wire_db):
    """The legitimate scope still permits the moderation state change."""
    db = _install(_wire_db, _admin_db({"reports.action": True}))

    result = _call(db)

    assert result["status"] == "resolved"
    assert len(db.updates) == 1
    assert "UPDATE reports" in db.updates[0]


def test_non_admin_is_still_denied_before_any_write(_wire_db):
    """The pre-existing admin-membership gate remains in force."""
    db = _install(_wire_db, _ScopeDB(admin_ids=[]))

    with pytest.raises(HTTPException) as exc:
        _call(db)

    assert exc.value.status_code == 403
    assert db.updates == []
