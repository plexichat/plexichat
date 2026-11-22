"""Report status gate checks collapse to a single DB round-trip.

``update_report_status`` used to issue one query for the admin check and a
second for report existence. Both are now resolved by one scalar-subquery
SELECT, preserving the 403-before-validation and 404-after-validation ordering.
"""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.features import reports as reports_route
from src.api.routes.features.reports import (
    ReportStatusUpdateRequest,
    update_report_status,
)
from src.core.admin.request_roles import clear_session_roles

ADMIN_ID = 4242


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.fetch_calls = []
        self.execute("CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username TEXT)")
        # The admin-role gate resolves the caller's scopes through
        # check_admin_permission -> _get_admin_roles, which issues fetch_all
        # against these two tables. Mirror the real schema so a role-bearing
        # admin can pass the gate instead of failing closed on a missing method.
        self.execute(
            "CREATE TABLE admin_roles (id INTEGER PRIMARY KEY, name TEXT, description TEXT, "
            "permissions TEXT, created_at INTEGER, created_by INTEGER, is_system INTEGER)"
        )
        self.execute(
            "CREATE TABLE admin_role_assignments (admin_id INTEGER, role_id INTEGER)"
        )
        self.execute(
            "CREATE TABLE reports (id INTEGER PRIMARY KEY, status TEXT, updated_at INTEGER, "
            "priority TEXT, assigned_to INTEGER, admin_notes TEXT, resolution TEXT, "
            "resolved_at INTEGER, resolved_by INTEGER, escalated_at INTEGER, "
            "reviewed_at INTEGER, reviewed_by INTEGER)"
        )

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def fetch_one(self, sql, params=()):
        self.fetch_calls.append(sql)
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=()):
        self.fetch_calls.append(sql)
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]


def _db(*, with_action_scope=True):
    # Roles cached from an earlier session lookup must not leak into this gate.
    clear_session_roles()
    db = _DB()
    db.execute(
        "INSERT INTO admin_users (id, username) VALUES (?, ?)", (ADMIN_ID, "root")
    )
    if with_action_scope:
        db.execute(
            "INSERT INTO admin_roles "
            "(id, name, description, permissions, created_at, created_by, is_system) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "reports-admin",
                "",
                json.dumps({"reports.action": True}),
                0,
                0,
                0,
            ),
        )
        db.execute(
            "INSERT INTO admin_role_assignments (admin_id, role_id) VALUES (?, ?)",
            (ADMIN_ID, 1),
        )
    db.execute("INSERT INTO reports (id, status) VALUES (1, 'open')")
    return db


def _call(db, user_id, status="investigating"):
    return asyncio.run(
        update_report_status(
            1,
            ReportStatusUpdateRequest(status=status),
            SimpleNamespace(user_id=user_id),
        )
    )


def test_admin_and_existence_resolved_in_one_query(monkeypatch):
    db = _db()
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)

    result = _call(db, ADMIN_ID)

    assert result["status"] == "investigating"
    gate_queries = [sql for sql in db.fetch_calls if "admin_users" in sql]
    assert len(gate_queries) == 1
    assert "report_exists" in gate_queries[0]
    assert not any(
        sql.strip().startswith("SELECT id FROM reports") for sql in db.fetch_calls
    )


def test_non_admin_still_gets_403(monkeypatch):
    db = _db()
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        _call(db, ADMIN_ID + 1)
    assert exc.value.status_code == 403


def test_missing_report_still_gets_404(monkeypatch):
    db = _db()
    db.execute("DELETE FROM reports WHERE id = 1")
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        _call(db, ADMIN_ID)
    assert exc.value.status_code == 404


def test_admin_without_action_scope_still_gets_403(monkeypatch):
    """Membership alone must not authorize a write; the role gate is load-bearing."""
    db = _db(with_action_scope=False)
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        _call(db, ADMIN_ID)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
