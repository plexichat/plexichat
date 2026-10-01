"""Reopen clears prior review metadata (Agent 23 F-BUG-3).

Reopening a report (PATCH .../status {"status": "open"}) must NULL out the
previous resolution/review fields so it no longer renders as
resolved/reviewed by the previous admin. Other transitions keep them.

The audit entry for this finding claimed "no reopen surface exists"; the
reopen path is live and must stay correct.
"""

from __future__ import annotations

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
    """Minimal sqlite adapter exposing the route's fetch_one/execute surface."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def _make_db() -> _DB:
    db = _DB()
    db.execute("CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username TEXT)")
    db.execute(
        "INSERT INTO admin_users (id, username) VALUES (?, ?)", (ADMIN_ID, "root")
    )
    db.execute(
        """CREATE TABLE reports (
               id INTEGER PRIMARY KEY,
               status TEXT,
               updated_at INTEGER,
               priority TEXT,
               assigned_to INTEGER,
               admin_notes TEXT,
               resolution TEXT,
               resolved_at INTEGER,
               resolved_by INTEGER,
               escalated_at INTEGER,
               reviewed_at INTEGER,
               reviewed_by INTEGER
           )"""
    )
    db.execute(
        """INSERT INTO reports
                (id, status, updated_at, priority, resolved_at, resolved_by,
                 reviewed_at, reviewed_by, resolution, escalated_at, assigned_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            1,
            "resolved",
            1000,
            "high",
            1111,
            ADMIN_ID,
            2222,
            ADMIN_ID,
            "handled",
            3333,
            777,
        ),
    )
    return db


def _call(db, monkeypatch, status: str):
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)
    body = ReportStatusUpdateRequest(status=status)
    current_user = SimpleNamespace(user_id=ADMIN_ID)
    return asyncio.run(update_report_status("1", body, current_user))


def _row(db):
    return db.fetch_one("SELECT * FROM reports WHERE id = 1", ())


def test_reopen_clears_resolution_and_review_metadata(monkeypatch):
    db = _make_db()

    result = _call(db, monkeypatch, "open")

    assert result["status"] == "open"
    row = _row(db)
    assert row["status"] == "open"
    assert row["resolution"] is None, "reopen must clear the prior resolution"
    assert row["resolved_at"] is None, "reopen must clear resolved_at"
    assert row["resolved_by"] is None, "reopen must clear resolved_by"
    assert row["reviewed_at"] is None, "reopen must clear reviewed_at"
    assert row["reviewed_by"] is None, "reopen must clear reviewed_by"
    assert row["escalated_at"] is None, "reopen must clear escalated_at"
    # Assignment is a report attribute, not lifecycle metadata: the same
    # admin keeps working the reopened report.
    assert row["assigned_to"] == 777, "reopen must retain the assignee"


def test_non_reopen_transition_keeps_existing_metadata(monkeypatch):
    db = _make_db()

    _call(db, monkeypatch, "investigating")

    row = _row(db)
    assert row["status"] == "investigating"
    assert row["resolved_at"] == 1111, "non-reopen transitions keep resolved_at"
    assert row["resolved_by"] == ADMIN_ID
    # "investigating" is itself a review transition, so reviewed_at is
    # refreshed (not cleared) and reviewed_by tracks the acting admin.
    assert row["reviewed_at"] is not None
    assert row["reviewed_by"] == ADMIN_ID
    assert row["resolution"] == "handled", "resolution survives a non-reopen move"
    assert row["escalated_at"] == 3333, "non-reopen transitions keep escalated_at"
    assert row["assigned_to"] == 777, "non-reopen transitions keep assigned_to"


def test_escalate_transition_sets_escalated_at(monkeypatch):
    db = _make_db()

    _call(db, monkeypatch, "escalated")

    row = _row(db)
    assert row["status"] == "escalated"
    assert row["escalated_at"] is not None, "escalating must stamp escalated_at"
    assert row["escalated_at"] != 3333, "escalating must refresh a stale stamp"


def test_unknown_report_returns_404(monkeypatch):
    # The UPDATE matches no rows for an unknown id; the endpoint must not echo
    # the requested status back as a success.
    db = _make_db()
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)
    body = ReportStatusUpdateRequest(status="resolved")
    current_user = SimpleNamespace(user_id=ADMIN_ID)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_report_status("999", body, current_user))

    assert exc.value.status_code == 404


def test_invalid_priority_returns_400_and_keeps_stored_value(monkeypatch):
    # Submit validates priority but the status update did not, so garbage
    # priority strings were stored in the moderation queue.
    db = _make_db()
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)
    body = ReportStatusUpdateRequest(status="investigating", priority="garbage")
    current_user = SimpleNamespace(user_id=ADMIN_ID)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_report_status("1", body, current_user))

    assert exc.value.status_code == 400
    assert _row(db)["priority"] == "high"


def test_valid_priority_update_is_stored(monkeypatch):
    db = _make_db()
    monkeypatch.setattr(reports_route.api, "get_db", lambda: db)
    body = ReportStatusUpdateRequest(status="investigating", priority="critical")
    current_user = SimpleNamespace(user_id=ADMIN_ID)

    result = asyncio.run(update_report_status("1", body, current_user))

    assert result["status"] == "investigating"
    assert _row(db)["priority"] == "critical"
