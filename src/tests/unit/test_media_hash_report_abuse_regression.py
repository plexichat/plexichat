"""Regression tests for hash-report throttle and reporter dedup.

``media.report_hash`` auto-blocks a hash once ``auto_block_threshold`` pending
reports exist (default 5). Without a per-user cap and reporter dedup, one
account could report the same hash five times and block that hash for the
whole instance, as well as flood the moderation queue. These tests pin the
boundary guards introduced in ``src/api/routes/media.py``.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import media as media_routes
from src.api.schemas.media import HashReportRequest


HASH = "a" * 64


def _body(**overrides):
    data = {"hash_value": HASH, "reason": "spam"}
    data.update(overrides)
    return HashReportRequest(**data)


def _user(uid=42):
    return SimpleNamespace(user_id=uid)


class TestHashReportRateLimit:
    def test_bucket_key_is_partitioned_per_user(self, monkeypatch):
        calls = []

        def fake_check(key, limit, window):
            calls.append((key, limit, window))
            return True, limit

        monkeypatch.setattr("src.core.database.check_rate_limit", fake_check)
        media_routes._check_hash_report_rate_limit(42)
        assert calls == [
            (
                "media:hash_report:42",
                media_routes.MAX_HASH_REPORTS_PER_MINUTE,
                media_routes.HASH_REPORT_WINDOW_SECONDS,
            )
        ]

    def test_exhausted_bucket_returns_429(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.database.check_rate_limit", lambda *a, **k: (False, 0)
        )
        with pytest.raises(HTTPException) as exc:
            media_routes._check_hash_report_rate_limit(42)
        assert exc.value.status_code == 429

    def test_storage_failure_fails_closed(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("limiter storage down")

        monkeypatch.setattr("src.core.database.check_rate_limit", boom)
        with pytest.raises(HTTPException) as exc:
            media_routes._check_hash_report_rate_limit(42)
        assert exc.value.status_code == 503


class TestHashReportDedup:
    def test_lookup_is_scoped_to_reporter_and_hash(self, monkeypatch):
        seen = []

        class FakeDB:
            def fetch_one(self, sql, params):
                seen.append(params)
                return None

        monkeypatch.setattr(media_routes.api, "get_db", lambda: FakeDB())
        assert media_routes._existing_pending_hash_report(42, HASH) is None
        assert seen == [(42, HASH)]

    def test_existing_pending_report_is_returned(self, monkeypatch):
        class FakeDB:
            def fetch_one(self, sql, params):
                return {"id": 999}

        monkeypatch.setattr(media_routes.api, "get_db", lambda: FakeDB())
        assert media_routes._existing_pending_hash_report(42, HASH) == 999

    def test_missing_database_fails_closed(self, monkeypatch):
        monkeypatch.setattr(media_routes.api, "get_db", lambda: None)
        with pytest.raises(HTTPException) as exc:
            media_routes._existing_pending_hash_report(42, HASH)
        assert exc.value.status_code == 503


class TestReportRouteWiring:
    def test_duplicate_does_not_reach_auto_block_counter(self, monkeypatch):
        created = []
        monkeypatch.setattr(
            media_routes, "_check_hash_report_rate_limit", lambda uid: None
        )
        monkeypatch.setattr(
            media_routes, "_existing_pending_hash_report", lambda uid, hv: 777
        )

        def fake_report_hash(**kwargs):
            created.append(kwargs)
            return 123

        monkeypatch.setattr("src.core.media.report_hash", fake_report_hash)

        response = asyncio.run(media_routes.report_content_hash(_body(), None, _user()))
        assert response.report_id == "777"
        assert response.message == "Report already submitted"
        assert created == []

    def test_fresh_report_creates_exactly_one_row(self, monkeypatch):
        created = []
        monkeypatch.setattr(
            media_routes, "_check_hash_report_rate_limit", lambda uid: None
        )
        monkeypatch.setattr(
            media_routes, "_existing_pending_hash_report", lambda uid, hv: None
        )

        def fake_report_hash(**kwargs):
            created.append(kwargs)
            return 123

        monkeypatch.setattr("src.core.media.report_hash", fake_report_hash)

        response = asyncio.run(media_routes.report_content_hash(_body(), None, _user()))
        assert response.report_id == "123"
        assert len(created) == 1
        assert created[0]["reporter_id"] == 42
        assert created[0]["hash_value"] == HASH
