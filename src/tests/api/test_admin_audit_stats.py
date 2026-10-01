"""Regression tests for the admin audit stats day boundary.

``admin_audit_log.created_at`` is stored as UTC millis (``int(time.time() *
1000)``), so the "today" boundary used by ``GET /audit/logs/stats`` must be
UTC midnight. A naive server-local ``datetime.now()`` midnight is off by the
host UTC offset on any non-UTC host.
"""

import os
import time
from datetime import timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def non_utc_host(monkeypatch):
    """Run the test as if the server lived in a non-UTC timezone."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    # monkeypatch restores TZ afterwards; re-sync the C library clock.
    time.tzset()


class _CapturingDB:
    """Fake DB that records the params passed to each count query."""

    def __init__(self):
        self.seen_params = []

    def fetch_one(self, query, params=None):
        self.seen_params.append(tuple(params or ()))
        return {"count": 0}


def _stats_client(fake_db):
    from src.api.routes.admin import audit as audit_routes

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    return client, ctx, audit_routes


def test_stats_today_boundary_is_utc_midnight(non_utc_host):
    """today_start must equal UTC midnight even on a non-UTC host."""
    from datetime import datetime as real_datetime

    fake_db = _CapturingDB()
    client, ctx, audit_routes = _stats_client(fake_db)
    try:
        # Noon UTC on 2026-06-15 (08:00 EDT in America/New_York).
        fixed_now = real_datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        class _FixedDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch.object(audit_routes, "datetime", _FixedDateTime):
            response = client.get("/api/v1/admin/audit/logs/stats")
        assert response.status_code == 200

        expected = int(
            real_datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert fake_db.seen_params, "expected the stats route to query the DB"
        for params in fake_db.seen_params:
            assert params[0] == expected
    finally:
        for c in reversed(ctx):
            c.stop()


def test_stats_counts_bucketed_by_action_family():
    """Sanity check: per-family counts only include families with rows."""
    fake_db = _CapturingDB()
    client, ctx, _ = _stats_client(fake_db)
    try:
        response = client.get("/api/v1/admin/audit/logs/stats")
        assert response.status_code == 200
        body = response.json()
        assert body["total_today"] == 0
        assert body["success_today"] == 0
        assert body["failed_today"] == 0
        assert body["by_action_family"] == {}
        # 3 totals + one count per action family.
        assert len(fake_db.seen_params) == 3 + 8
    finally:
        for c in reversed(ctx):
            c.stop()


def test_tz_fixture_restores_utc():
    """Guard: the non-UTC fixture above must not leak TZ into other tests."""
    assert os.environ.get("TZ") != "America/New_York"


class _ExportDB:
    """Fake DB returning a fixed audit row for the CSV export."""

    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, query, params=None):
        return self._rows


def test_export_csv_timestamp_is_utc(non_utc_host):
    """The CSV Timestamp column must be UTC-aware, not naive server-local."""
    from datetime import datetime

    from src.api.routes.admin import audit as audit_routes

    epoch_ms = int(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    row = {
        "id": 1,
        "admin_id": 2,
        "action": "role.grant",
        "target_type": "admin_user",
        "target_id": "3",
        "details": "",
        "ip_address": "1.2.3.4",
        "user_agent": "ua",
        "status": "success",
        "created_at": epoch_ms,
    }
    fake_db = _ExportDB([row])

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        response = client.get("/api/v1/admin/audit/logs/export?format=csv")
        assert response.status_code == 200
        # UTC noon; on the America/New_York host this is 08:00 local.
        assert "2026-06-15T12:00:00+00:00" in response.text
    finally:
        for c in reversed(ctx):
            c.stop()


class _ListDB:
    """Fake DB for the paginated list: a count plus fixed rows."""

    def __init__(self, rows):
        self._rows = rows

    def fetch_one(self, query, params=None):
        return {"total": len(self._rows)}

    def fetch_all(self, query, params=None):
        return self._rows


def test_list_timestamp_is_utc(non_utc_host):
    """GET /audit/logs must render ``timestamp`` as UTC, like the export."""
    from datetime import datetime

    from src.api.routes.admin import audit as audit_routes

    epoch_ms = int(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    row = {
        "id": 1,
        "admin_id": 2,
        "action": "role.grant",
        "target_type": "admin_user",
        "target_id": "3",
        "details": "",
        "ip_address": "1.2.3.4",
        "user_agent": "ua",
        "status": "success",
        "created_at": epoch_ms,
    }
    fake_db = _ListDB([row])

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=fake_db),
    )
    for c in ctx:
        c.start()
    try:
        response = client.get("/api/v1/admin/audit/logs")
        assert response.status_code == 200
        # UTC noon; a naive server-local render on the non-UTC host would be 08:00.
        assert response.json()["logs"][0]["timestamp"] == "2026-06-15T12:00:00+00:00"
    finally:
        for c in reversed(ctx):
            c.stop()
