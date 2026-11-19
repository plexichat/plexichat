"""Regression: audit timestamp query params must be bindable SQLite integers.

``date_from``/``date_to`` are parsed by Pydantic as arbitrary-precision ints.
A value wider than a signed 64-bit INTEGER used to reach the DB and raise
``OverflowError`` on bind, turning a malformed request into a 500.
"""

import sqlite3
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.routes.admin import audit as audit_routes


class _SQLiteDB:
    """Minimal real SQL DB so an out-of-range bind actually overflows."""

    def __init__(self):
        # FastAPI runs sync handlers in a threadpool; allow cross-thread use.
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE admin_audit_log ("
            "id INTEGER, admin_id INTEGER, action TEXT, target_type TEXT, "
            "target_id INTEGER, details TEXT, ip_address TEXT, "
            "user_agent TEXT, status TEXT, created_at INTEGER)"
        )

    def fetch_one(self, query, params=None):
        row = self.conn.execute(query, params or ()).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, query, params=None):
        return [dict(r) for r in self.conn.execute(query, params or ()).fetchall()]


def _client():
    db = _SQLiteDB()
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=db),
    )
    for c in ctx:
        c.start()
    return client, ctx, db


@pytest.mark.parametrize(
    "param",
    [
        "date_from=99999999999999999999",  # 20 digits, > int64 max
        "date_to=9223372036854775808",  # 2**63, one past int64 max
        "date_from=-99999999999999999999",
    ],
)
def test_out_of_range_timestamp_rejected(param):
    client, ctx, db = _client()
    try:
        response = client.get(f"/api/v1/admin/audit/logs?{param}")
        assert response.status_code == 400, response.text
    finally:
        for c in reversed(ctx):
            c.stop()


@pytest.mark.parametrize(
    "param",
    [
        "date_from=0",  # epoch is a real, valid lower bound
        "date_to=9223372036854775807",  # int64 max is bindable
        "date_from=1700000000000&date_to=1700000010000",
    ],
)
def test_in_range_timestamp_still_works(param):
    client, ctx, _ = _client()
    try:
        assert client.get(f"/api/v1/admin/audit/logs?{param}").status_code == 200
    finally:
        for c in reversed(ctx):
            c.stop()


def test_validate_date_window_helper_rejects_out_of_range():
    for bad in (10**30, 2**63, -1):
        with pytest.raises(HTTPException) as exc:
            audit_routes._validate_date_window(bad, None)
        assert exc.value.status_code == 400
