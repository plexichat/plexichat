"""Regressions for two deep admin hardening gaps.

* Audit scans were only bounded when no filter was supplied. An explicit
  ``date_from=0`` (or any pre-retention bound, or an upper-only window) left
  the count/export queries free to scan the entire audit table.
* ``approve_request`` checked ``expires_at`` on the fetched snapshot only, so a
  request that expired between the advisory SELECT and the conditional UPDATE
  could still be flipped to 'approved' (TOCTOU).

Run with: pytest -m api src/tests/api/test_admin_scan_and_vote_toctou_regressions.py
"""

import asyncio
import re
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam


class _RecordingDB:
    def __init__(self):
        self.calls = []

    def fetch_one(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        return {"total": 0}

    def fetch_all(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        return []


def _audit_client(monkeypatch, config=None):
    from src.api.routes.admin import audit as audit_routes

    monkeypatch.setattr(audit_routes, "_audit_config", lambda: config or {})
    db = _RecordingDB()
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=db),
    )
    for c in ctx:
        c.start()
    return TestClient(app), ctx, db


def _retention_cutoff_bounds():
    now_ms = int(time.time() * 1000)
    return now_ms - 367 * 86400 * 1000, now_ms


class TestAuditScansAreBounded:
    def test_epoch_lower_bound_gets_a_bounded_retention_cutoff(self, monkeypatch):
        """``date_from=0`` must not turn into a whole-table scan."""
        client, ctx, db = _audit_client(monkeypatch)
        try:
            assert client.get("/api/v1/admin/audit/logs?date_from=0").status_code == 200
            count_sql, count_params = db.calls[0]
            # The caller's explicit epoch bound is still passed through.
            assert count_params == (0,)
            literals = [int(x) for x in re.findall(r"created_at >= (\d+)", count_sql)]
            assert literals, "a retention cutoff must be added to the SQL"
            low, high = _retention_cutoff_bounds()
            assert low <= literals[0] <= high
        finally:
            for c in reversed(ctx):
                c.stop()

    def test_export_epoch_lower_bound_is_bounded_too(self, monkeypatch):
        client, ctx, db = _audit_client(monkeypatch)
        try:
            assert (
                client.get(
                    "/api/v1/admin/audit/logs/export?date_from=0&format=csv"
                ).status_code
                == 200
            )
            export_sql, export_params = db.calls[0]
            assert export_params == (0, 10_000)
            literals = [int(x) for x in re.findall(r"created_at >= (\d+)", export_sql)]
            assert literals
            low, high = _retention_cutoff_bounds()
            assert low <= literals[0] <= high
        finally:
            for c in reversed(ctx):
                c.stop()

    def test_upper_only_window_gets_a_lower_bound(self, monkeypatch):
        client, ctx, db = _audit_client(monkeypatch)
        try:
            assert (
                client.get("/api/v1/admin/audit/logs?date_to=9999999999999").status_code
                == 200
            )
            count_sql, count_params = db.calls[0]
            assert count_params == (9999999999999,)
            assert "created_at <= ?" in count_sql
            literals = [int(x) for x in re.findall(r"created_at >= (\d+)", count_sql)]
            assert literals
            low, high = _retention_cutoff_bounds()
            assert low <= literals[0] <= high
        finally:
            for c in reversed(ctx):
                c.stop()

    def test_filtered_scans_stay_bounded_in_strict_mode(self, monkeypatch):
        client, ctx, db = _audit_client(monkeypatch, {"require_indexed_filter": True})
        try:
            assert (
                client.get("/api/v1/admin/audit/logs?action_family=user").status_code
                == 200
            )
            count_sql, count_params = db.calls[0]
            assert "action LIKE ?" in count_sql
            assert count_params == ("user%",)
            literals = [int(x) for x in re.findall(r"created_at >= (\d+)", count_sql)]
            assert literals
            low, high = _retention_cutoff_bounds()
            assert low <= literals[0] <= high
        finally:
            for c in reversed(ctx):
                c.stop()


def _approval_request():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/approvals/1/approve",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


class _Cursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _VoteDB:
    """Controlled approval row; the guarded UPDATE can be made to lose."""

    def __init__(self, expire_vote=False):
        self.expire_vote = expire_vote
        self.calls = []
        self.reads = 0

    def fetch_one(self, query, params=None):
        self.reads += 1
        self.calls.append(("read", query, tuple(params or ())))
        # The first read is the stale (unexpired) snapshot. If the guarded
        # UPDATE loses, the retry re-reads the real, expired row.
        expires_at = (
            1
            if self.expire_vote and self.reads > 1
            else int(time.time() * 1000) + 3_600_000
        )
        return {
            "id": 1,
            "requested_by": 2,
            "status": "pending",
            "current_approvals": 0,
            "required_approvals": 2,
            "approved_by": None,
            "expires_at": expires_at,
        }

    def execute(self, query, params=None):
        self.calls.append(("write", query, tuple(params or ())))
        return _Cursor(0 if self.expire_vote else 1)


def _run_vote(db):
    from src.api.routes.admin import approvals as routes

    with (
        patch.object(routes, "check_host_restriction"),
        patch.object(routes, "require_admin_permission", return_value=3),
        patch("src.api.get_db", return_value=db),
        patch("src.utils.system_alerts.record_system_alert"),
        patch("src.core.admin.permissions.log_admin_action") as audit,
    ):
        result = asyncio.run(
            routes.approve_request(_approval_request(), SnowflakeIDParam(1))
        )
    return result, audit


class TestApprovalVoteExpiryGuard:
    def test_update_carries_an_atomic_expiry_guard(self):
        db = _VoteDB()
        _run_vote(db)
        write_calls = [c for c in db.calls if c[0] == "write"]
        assert len(write_calls) == 1
        _, write_sql, write_params = write_calls[0]
        assert "(expires_at IS NULL OR expires_at > ?)" in write_sql
        # Placeholder order: approved_by, updated_at, id, expiry, raw, raw.
        guard = write_params[3]
        assert abs(guard - int(time.time() * 1000)) < 60_000

    def test_expiry_between_read_and_write_cannot_authorize_a_vote(self):
        db = _VoteDB(expire_vote=True)
        from src.api.routes.admin import approvals as routes

        with (
            patch.object(routes, "check_host_restriction"),
            patch.object(routes, "require_admin_permission", return_value=3),
            patch("src.api.get_db", return_value=db),
            patch("src.core.admin.permissions.log_admin_action") as audit,
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    routes.approve_request(_approval_request(), SnowflakeIDParam(1))
                )
        assert exc.value.status_code == 400
        assert "expired" in str(exc.value.detail).lower()
        audit.assert_not_called()
