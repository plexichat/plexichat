"""Regression: the admin audit list COUNT is collapsed across dashboard polls.

``GET /admin/audit/logs`` runs a covering-index ``COUNT(*)`` over the whole
retention window on every request. The operator dashboard polls it repeatedly,
so the same count was recomputed over and over. It is now served through the
short-TTL, DB-scoped summary cache (the same one the stats endpoint uses), so a
burst of identical polls computes the count once. These tests lock in that
behaviour plus the safety invariants: the cache is keyed by filter, scoped to
the backing DB, and never bypasses the host/permission gates.

Run with: pytest -n 0 src/tests/api/test_admin_audit_list_count_cache.py
"""

from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin import _summary_cache
from src.api.routes.admin import audit as audit_routes

_NAMESPACE = "audit_list_count"


class _CountingDB:
    """Fake DB that records how many COUNT scans a list request performs."""

    def __init__(self, total=1234):
        self.total = total
        self.count_calls = 0
        self.data_calls = 0

    def fetch_one(self, query, params=None):
        if "COUNT(*)" in query:
            self.count_calls += 1
            return {"total": self.total}
        return {}

    def fetch_all(self, query, params=None):
        self.data_calls += 1
        return []


def _client(db, permission=None):
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    permission = permission or Mock(return_value=1)
    patches = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission", permission),
        patch("src.api.get_db", return_value=db),
    )
    for patcher in patches:
        patcher.start()
    return TestClient(app), patches, permission


def _stop(patches):
    for patcher in reversed(patches):
        patcher.stop()


def test_repeated_identical_polls_share_one_count_scan():
    _summary_cache.clear(_NAMESPACE)
    db = _CountingDB()
    client, patches, _ = _client(db)
    try:
        first = client.get("/api/v1/admin/audit/logs")
        second = client.get("/api/v1/admin/audit/logs")
        assert first.status_code == second.status_code == 200
        # The count is computed once; both responses still carry the exact
        # total/pages, and the bounded data page is fetched on every poll.
        assert db.count_calls == 1
        assert db.data_calls == 2
        assert first.json()["total"] == second.json()["total"] == 1234
        assert first.json()["pages"] == second.json()["pages"] == 25
    finally:
        _stop(patches)
        _summary_cache.clear(_NAMESPACE)


def test_different_filters_do_not_share_a_count():
    _summary_cache.clear(_NAMESPACE)
    db = _CountingDB()
    client, patches, _ = _client(db)
    try:
        assert client.get("/api/v1/admin/audit/logs?status=success").status_code == 200
        assert client.get("/api/v1/admin/audit/logs?status=failed").status_code == 200
        # Distinct filter keys must not borrow each other's count.
        assert db.count_calls == 2
    finally:
        _stop(patches)
        _summary_cache.clear(_NAMESPACE)


def test_count_cache_is_scoped_to_the_backing_db():
    _summary_cache.clear(_NAMESPACE)
    db_a, db_b = _CountingDB(), _CountingDB()
    client_a, patches_a, _ = _client(db_a)
    try:
        assert client_a.get("/api/v1/admin/audit/logs").status_code == 200
        # Repoint the DB at a different instance: its count must be computed
        # from scratch, never borrowed from the previous database's cache.
        patches_a[2].stop()
        patches_a = (
            patches_a[0],
            patches_a[1],
            patch("src.api.get_db", return_value=db_b),
        )
        patches_a[2].start()
        assert client_a.get("/api/v1/admin/audit/logs").status_code == 200
    finally:
        _stop(patches_a)
        _summary_cache.clear(_NAMESPACE)
    assert db_a.count_calls == 1
    assert db_b.count_calls == 1


def test_permission_gate_still_runs_on_a_cache_hit():
    _summary_cache.clear(_NAMESPACE)
    db = _CountingDB()
    permission = Mock(return_value=1)
    client, patches, _ = _client(db, permission=permission)
    try:
        assert client.get("/api/v1/admin/audit/logs").status_code == 200
        assert client.get("/api/v1/admin/audit/logs").status_code == 200
        # Authorization is re-checked on every request, including cache hits.
        assert permission.call_count == 2
        assert db.count_calls == 1
    finally:
        _stop(patches)
        _summary_cache.clear(_NAMESPACE)
