"""Report status gate checks collapse to a single DB round-trip.

``update_report_status`` used to issue one query for the admin check and a
second for report existence. Both are now resolved by one scalar-subquery
SELECT, preserving the 403-before-validation and 404-after-validation ordering.
"""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.features import reports as reports_route
from src.api.routes.features.reports import (
    ReportStatusUpdateRequest,
    update_report_status,
)

ADMIN_ID = 4242


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.fetch_calls = []
        self.execute("CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username TEXT)")
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


def _db():
    db = _DB()
    db.execute(
        "INSERT INTO admin_users (id, username) VALUES (?, ?)", (ADMIN_ID, "root")
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
