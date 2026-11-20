"""Regression tests for the short-TTL cache on GET /audit/logs/stats.

The stats handler issues eleven COUNT scans over ``admin_audit_log`` per
request.  Operators poll it from the dashboard every few seconds, so the
summary is cached briefly and invalidated on audit writes.  These tests pin the
cache-hit behaviour, TTL expiry, invalidation, DB scoping and the invariant
that authorization gates run on every request (including cache hits).
"""

import time
from datetime import datetime as real_datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import _summary_cache, audit as audit_routes
from src.core.admin.logging import AdminLogEntry, AdminLogger

_STATS_URL = "/api/v1/admin/audit/logs/stats"
_FIXED_NOW = real_datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class _FixedDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FIXED_NOW.replace(tzinfo=None)
        return _FIXED_NOW.astimezone(tz)


class CountingDB:
    """Fake DB that counts the route's COUNT queries and records writes."""

    def __init__(self, count=5):
        self.fetch_one_calls = 0
        self.executed = []
        self.count = count

    def fetch_one(self, query, params=None):
        self.fetch_one_calls += 1
        return {"count": self.count}

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def column_exists(self, table, column):
        return True


@pytest.fixture
def stats_env():
    _summary_cache.clear("audit_stats")
    db = CountingDB()
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    guards = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=db),
        patch.object(audit_routes, "datetime", _FixedDateTime),
    )
    mocks = tuple(guard.start() for guard in guards)
    yield client, db, mocks
    for guard in reversed(guards):
        guard.stop()
    _summary_cache.clear("audit_stats")


def _expire_audit_stats_entries():
    """Force every cached audit-stats entry past its expiry (white-box)."""
    now = time.monotonic()
    with _summary_cache._LOCK:
        for key, (expiry, value) in list(_summary_cache._ENTRIES.items()):
            if key[0] == "audit_stats":
                _summary_cache._ENTRIES[key] = (now - 1.0, value)


def test_stats_cache_coalesces_polls(stats_env):
    """Repeated polls within the TTL reuse the first computation."""
    client, db, _ = stats_env
    first = client.get(_STATS_URL)
    assert first.status_code == 200
    assert db.fetch_one_calls == 11  # 3 totals + 8 action families
    expected = first.json()

    second = client.get(_STATS_URL)
    assert second.status_code == 200
    assert second.json() == expected
    assert db.fetch_one_calls == 11  # zero new queries on the cache hit


def test_stats_cache_expires_and_recomputes(stats_env):
    """Once the TTL elapses the next poll recomputes the aggregate."""
    client, db, _ = stats_env
    client.get(_STATS_URL)
    assert db.fetch_one_calls == 11

    _expire_audit_stats_entries()
    client.get(_STATS_URL)
    assert db.fetch_one_calls == 22


def test_audit_write_invalidates_stats_cache(stats_env):
    """A successful audit-log write drops the cached summary immediately."""
    client, db, _ = stats_env
    client.get(_STATS_URL)
    assert db.fetch_one_calls == 11

    logger = AdminLogger(file_logger=None)
    written = logger.log_action(
        db,
        AdminLogEntry(admin_id=42, action="user.ban", status="success"),
        log_to_file=False,
    )
    assert written is True
    assert db.executed, "the audit row should have been written"

    client.get(_STATS_URL)
    assert db.fetch_one_calls == 22  # recomputed after invalidation


def test_cache_scoped_to_database(stats_env):
    """A different backing DB must not observe another DB's cached summary."""
    client, db, _ = stats_env
    client.get(_STATS_URL)
    assert db.fetch_one_calls == 11

    other = CountingDB(count=9)
    with patch("src.api.get_db", return_value=other):
        resp = client.get(_STATS_URL)
    assert resp.json()["total_today"] == 9
    assert other.fetch_one_calls == 11
    assert db.fetch_one_calls == 11  # untouched


def test_authorization_gates_run_on_cache_hits(stats_env):
    """A cached summary never bypasses the host/permission checks."""
    client, _, guards = stats_env
    host_guard, perm_guard = guards[0], guards[1]

    client.get(_STATS_URL)  # miss
    client.get(_STATS_URL)  # hit

    assert host_guard.call_count == 2
    assert perm_guard.call_count == 2
    # The scope check is repeated on the cache hit too, not skipped.
    assert perm_guard.call_args.args[1] == "admin.audit.read"
