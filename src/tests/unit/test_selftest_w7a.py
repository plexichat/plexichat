"""Unit tests for the self-test disposable-DB guard, artifact journal,
and false-pass fixes (Agent 25 W7-A).
"""

import os
from types import SimpleNamespace

import pytest


class _FakeDB:
    type = "sqlite"
    disposable = False

    def __init__(self, path="/tmp/live_production.db"):
        self.config = {"path": path}
        self._rows = []

    def fetch_one(self, query, params=None):
        return None

    def execute(self, query, params=None):
        self._rows.append((query, params))
        return None

    def fetch_all(self, query, params=None):
        return []


@pytest.mark.unit
class TestDisposableGuard:
    def test_plain_db_is_refused(self):
        from src.core.selftest.db_guard import is_disposable_database

        allowed, reason = is_disposable_database(_FakeDB(), {})
        assert allowed is False
        assert "disposable" in reason

    def test_env_marker_allows(self):
        from src.core.selftest.db_guard import (
            DISPOSABLE_ENV_MARKER,
            is_disposable_database,
        )

        os.environ[DISPOSABLE_ENV_MARKER] = "1"
        try:
            allowed, _ = is_disposable_database(_FakeDB(), {})
            assert allowed is True
        finally:
            del os.environ[DISPOSABLE_ENV_MARKER]

    def test_explicit_opt_out_allows(self):
        from src.core.selftest.db_guard import is_disposable_database

        allowed, reason = is_disposable_database(
            _FakeDB(), {"require_disposable_db": False}
        )
        assert allowed is True
        assert "opt-out" in reason

    def test_attribute_flag_allows(self):
        from src.core.selftest.db_guard import is_disposable_database

        db = _FakeDB()
        db.disposable = True
        assert is_disposable_database(db, {})[0] is True

    def test_selftest_path_allows(self):
        from src.core.selftest.db_guard import is_disposable_database

        db = _FakeDB(path="/data/selftest_plexichat.sqlite")
        assert is_disposable_database(db, {})[0] is True


@pytest.mark.unit
class TestArtifactJournal:
    def test_record_survives_reload_and_reconciles(self, tmp_path):
        from src.core.selftest.artifact_journal import ArtifactJournal

        path = str(tmp_path / "journal.json")
        journal = ArtifactJournal(path)
        journal.record("permission_grant", user_id=42, permissions=["admin.*", "*"])
        journal.record("elevated_account", user_id=43, username="selftest_other")

        # A fresh instance (simulating a post-crash run) sees both entries.
        reloaded = ArtifactJournal(path)
        assert len(reloaded.pending()) == 2

        db = _FakeDB()
        touched = reloaded.reconcile(db)
        assert touched == 6  # 3 scoped statements per recorded user
        # All reconciliation SQL must be scoped to selftest-owned accounts.
        for query, params in db._rows:
            if query.startswith("UPDATE") or query.startswith("DELETE"):
                assert "selftest_%" in query or query.startswith(
                    "DELETE FROM admin_role_assignments"
                ), query
        assert reloaded.pending() == []
        assert ArtifactJournal(path).pending() == []


@pytest.mark.unit
class TestRateLimitHonestSuccess:
    def test_success_reflects_observed_rate_limiting(self):
        from src.core.selftest.services.ratelimit import RateLimitTester

        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code
                self.headers = {}

        responses = iter([FakeResponse(200), FakeResponse(200), FakeResponse(200)])

        class FakeSession:
            def get(self, _target, timeout):
                return next(responses)

        session = FakeSession()
        ctx = SimpleNamespace(
            make_session=lambda: session,
            base_url="http://127.0.0.1:8000",
            results=[],
        )
        RateLimitTester(ctx).test_rate_limits()
        result = ctx.results[0]
        # No 429 observed -> the check must NOT be a pass.
        assert result["success"] is False
        assert result["status_code"] == 200


@pytest.mark.unit
class TestReportSkips:
    def test_skips_are_not_passes_or_failures(self):
        from src.core.selftest.services.report import ReportGenerator

        ctx = SimpleNamespace(
            results=[
                {
                    "method": "GET",
                    "path": "/a",
                    "success": True,
                    "duration_ms": 1,
                    "status_code": 200,
                },
                {
                    "method": "GET",
                    "path": "/b",
                    "success": False,
                    "skipped": True,
                    "duration_ms": 0,
                    "status_code": 404,
                },
                {
                    "method": "GET",
                    "path": "/c",
                    "success": False,
                    "duration_ms": 0,
                    "status_code": 500,
                },
                {
                    "method": "GET",
                    "path": "/d",
                    "success": False,
                    "skipped": True,
                    "duration_ms": 0,
                    "status_code": 404,
                    "label": "ws_dispatch",
                },
            ],
            start_time=0.0,
            ram_monitor=None,
        )
        report = ReportGenerator(ctx)
        # Skips are neither passes nor failures -> suite outcome is False
        # only because of the single genuine failure.
        assert report.report_summary() is False
