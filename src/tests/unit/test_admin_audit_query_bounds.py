"""Regression: audit queries are bounded and need an indexed predicate.

An unfiltered ``SELECT * FROM admin_audit_log`` turns a single admin request
into a full-table scan (twice, because of the count query). The route now
requires an indexed filter -- hard-rejecting in strict mode, otherwise
injecting a bounded ``created_at`` lower bound -- and caps pages/date windows.
The store's own ``query()`` refuses selector-less full scans as well.
"""

import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.security.hardening import HardeningViolation
from src.core.telemetry.audit import AuditChain


def _chain(tmp_path):
    chain = AuditChain(
        str(tmp_path / "audit.jsonl"),
        head_path=str(tmp_path / "audit.jsonl.head"),
        durable=False,
    )
    chain.append("user.ban", 42, target_id=7)
    chain.append("role.assign", 42, target_id=8)
    return chain


def test_store_query_requires_a_selector(tmp_path):
    chain = _chain(tmp_path)
    with pytest.raises(HardeningViolation):
        chain.query()
    rows = chain.query(action="user.ban")
    assert len(rows) == 1
    assert rows[0]["entry"]["action"] == "user.ban"


def test_store_query_bounds_the_window(tmp_path):
    chain = _chain(tmp_path)
    with pytest.raises(HardeningViolation):
        chain.query(since_ms=0, until_ms=367 * 24 * 60 * 60 * 1000)


class _RecordingDB:
    def __init__(self):
        self.calls = []

    def fetch_one(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        return {"total": 0}

    def fetch_all(self, query, params=None):
        self.calls.append((query, tuple(params or ())))
        return []


def _client(monkeypatch=None, config=None):
    from src.api.routes.admin import audit as audit_routes

    if monkeypatch is not None:
        monkeypatch.setattr(audit_routes, "_audit_config", lambda: config or {})
    db = _RecordingDB()
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


def test_unfiltered_request_injects_a_bounded_indexed_predicate(monkeypatch):
    client, ctx, db = _client(monkeypatch)
    try:
        response = client.get("/api/v1/admin/audit/logs")
        assert response.status_code == 200
        assert db.calls, "expected the route to query the DB"
        count_query, count_params = db.calls[0]
        assert "created_at >= ?" in count_query
        cutoff = count_params[0]
        # Bound is roughly "one retention year ago", never epoch/unbounded.
        now_ms = int(time.time() * 1000)
        assert now_ms - 367 * 86400 * 1000 <= cutoff <= now_ms - 364 * 86400 * 1000
    finally:
        for c in reversed(ctx):
            c.stop()


def test_strict_mode_rejects_unfiltered_scans(monkeypatch):
    client, ctx, _ = _client(monkeypatch, {"require_indexed_filter": True})
    try:
        assert client.get("/api/v1/admin/audit/logs").status_code == 400
        assert client.get("/api/v1/admin/audit/logs/export").status_code == 400
        # A filtered request is still allowed in strict mode.
        assert (
            client.get("/api/v1/admin/audit/logs?action_family=user").status_code == 200
        )
    finally:
        for c in reversed(ctx):
            c.stop()


def test_date_window_is_bounded(monkeypatch):
    client, ctx, _ = _client(monkeypatch)
    try:
        wide = 400 * 24 * 60 * 60 * 1000
        response = client.get(
            "/api/v1/admin/audit/logs?date_from=1000&date_to=" + str(1000 + wide)
        )
        assert response.status_code == 400
        inverted = client.get("/api/v1/admin/audit/logs?date_from=5000&date_to=1000")
        assert inverted.status_code == 400
    finally:
        for c in reversed(ctx):
            c.stop()


def test_page_depth_is_capped(monkeypatch):
    client, ctx, _ = _client(monkeypatch)
    try:
        assert client.get("/api/v1/admin/audit/logs?page=10001").status_code == 422
    finally:
        for c in reversed(ctx):
            c.stop()
