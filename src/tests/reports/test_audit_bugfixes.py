"""
Tests for audit bug fixes in the reports system.

Covers:
- REP-2: the divergent duplicate schema was removed from reports.__init__
- REP-3: enhanced reports reject self-reports (user and own message)
- REP-4: enhanced reports validate target_type and target existence
- REP-5: enhanced reports validate evidence URLs and return 404 for missing reports
- REP-6: missing enhanced report returns 404, not 400
"""

import asyncio
import pytest

from src.core import reports as reports_module
from src.core.reports import schema as reports_schema
from src.api.routes.features.reports import (
    submit_enhanced_report,
    get_report_details,
    EnhancedReportRequest,
)


class TestRep2SchemaDelegation:
    """REP-2: no divergent duplicate schema in reports.__init__."""

    def test_init_has_no_divergent_schema(self):
        # The duplicate SCHEMA block was removed; creation now delegates to
        # the canonical schema.py definition.
        assert not hasattr(reports_module, "SCHEMA")

    def test_canonical_schema_has_reports_table(self):
        assert "CREATE TABLE IF NOT EXISTS reports" in reports_schema.SCHEMA

    def test_create_tables_is_callable(self):
        assert callable(reports_module.create_tables)


class _FakeUser:
    def __init__(self, user_id):
        self.user_id = user_id


class _FakeRow(dict):
    pass


class _FakeDB:
    """Minimal DB stub for the enhanced-report endpoint."""

    def __init__(self, user_exists=True, report_row=None):
        self.user_exists = user_exists
        self.report_row = report_row
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return None

    def fetch_one(self, query, params=None):
        if "FROM auth_users" in query:
            return {"id": params[0]} if self.user_exists else None
        if "FROM reports" in query:
            return self.report_row
        return None


class _FakeMessage:
    def __init__(self, author_id, content="hello"):
        self.author_id = author_id
        self.content = content


class _FakeMessaging:
    def __init__(self, message=None):
        self._message = message

    def get_message(self, user_id, message_id):
        return self._message


def _patch_api(monkeypatch, db, messaging):
    import src.api as api_module

    monkeypatch.setattr(api_module, "get_db", lambda: db)
    monkeypatch.setattr(api_module, "get_messaging", lambda: messaging)


class TestRep345EnhancedReportValidation:
    """REP-3 / REP-4 / REP-5: enhanced report input validation."""

    def test_invalid_target_type_rejected(self, monkeypatch):
        db = _FakeDB()
        _patch_api(monkeypatch, db, None)
        req = EnhancedReportRequest(target_type="server", target_id="5", reason="spam")
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, _FakeUser(1)))
        assert exc.value.status_code == 400

    def test_self_user_report_rejected(self, monkeypatch):
        db = _FakeDB(user_exists=True)
        _patch_api(monkeypatch, db, None)
        req = EnhancedReportRequest(target_type="user", target_id="5", reason="spam")
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, _FakeUser(5)))
        assert exc.value.status_code == 400

    def test_unknown_user_report_rejected(self, monkeypatch):
        db = _FakeDB(user_exists=False)
        _patch_api(monkeypatch, db, None)
        req = EnhancedReportRequest(target_type="user", target_id="999", reason="spam")
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, _FakeUser(1)))
        assert exc.value.status_code == 400

    def test_invalid_evidence_url_rejected(self, monkeypatch):
        db = _FakeDB(user_exists=True)
        _patch_api(monkeypatch, db, None)
        req = EnhancedReportRequest(
            target_type="user",
            target_id="999",
            reason="spam",
            evidence_urls=["javascript:alert(1)"],
        )
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, _FakeUser(1)))
        assert exc.value.status_code == 400

    def test_self_message_report_rejected(self, monkeypatch):
        db = _FakeDB()
        messaging = _FakeMessaging(message=_FakeMessage(author_id=5))
        _patch_api(monkeypatch, db, messaging)
        req = EnhancedReportRequest(
            target_type="message",
            target_id="100",
            reason="spam",
            channel_id="1",
            server_id="2",
        )
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, _FakeUser(5)))
        assert exc.value.status_code == 400

    def test_missing_message_report_rejected(self, monkeypatch):
        db = _FakeDB()
        messaging = _FakeMessaging(message=None)
        _patch_api(monkeypatch, db, messaging)
        req = EnhancedReportRequest(
            target_type="message",
            target_id="100",
            reason="spam",
            channel_id="1",
            server_id="2",
        )
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, _FakeUser(5)))
        assert exc.value.status_code == 400

    def test_enhanced_report_rate_limit_rejects_flood(self, monkeypatch):
        db = _FakeDB(user_exists=True)
        _patch_api(monkeypatch, db, None)
        monkeypatch.setattr(
            "src.core.database.check_rate_limit", lambda *args: (False, 0)
        )
        req = EnhancedReportRequest(target_type="user", target_id="999", reason="spam")
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, _FakeUser(1)))
        assert exc.value.status_code == 429
        assert db.executed == []

    def test_enhanced_report_deduplicates_existing_open_report(self, monkeypatch):
        db = _FakeDB(user_exists=True, report_row={"id": 42})
        _patch_api(monkeypatch, db, None)
        monkeypatch.setattr(
            "src.core.database.check_rate_limit", lambda *args: (True, 9)
        )
        req = EnhancedReportRequest(target_type="user", target_id="999", reason="spam")
        result = asyncio.run(submit_enhanced_report(req, _FakeUser(1)))
        assert result["success"] is True
        assert result["report_id"] == "42"
        assert result["message"] == "Report already submitted"
        assert db.executed == []

    def test_valid_user_report_accepted(self, monkeypatch):
        db = _FakeDB(user_exists=True)
        _patch_api(monkeypatch, db, None)
        req = EnhancedReportRequest(target_type="user", target_id="999", reason="spam")
        result = asyncio.run(submit_enhanced_report(req, _FakeUser(1)))
        assert result["success"] is True
        assert "INSERT INTO reports" in db.executed[0][0]


class TestRep6MissingReportNotFound:
    """REP-6: a missing enhanced report returns 404, not 400."""

    def test_missing_report_returns_404(self, monkeypatch):
        db = _FakeDB(report_row=None)
        _patch_api(monkeypatch, db, None)
        with pytest.raises(Exception) as exc:
            asyncio.run(get_report_details(12345, _FakeUser(1)))
        assert exc.value.status_code == 404
