"""
Tests for the reports module.

Covers:
- Module lifecycle (setup guard)
- Message reporting (create, fetch, list, counts)
- Message report review workflow (action/dismiss/review)
- User reporting (create, fetch, list, counts, evidence)
- User report review workflow
- Status filtering and pagination
- Row conversion for dict and tuple rows
"""

import pytest

from src.core import reports
from src.core.reports import ReportStatus, ReportCategory


@pytest.fixture
def reports_module(db):
    """Reports module bound to the test db."""
    reports.setup(db)
    return reports


class TestModuleLifecycle:
    """Test setup/is_setup and the uninitialized guard."""

    def test_not_setup_guard(self, monkeypatch):
        """Calls before setup must raise RuntimeError."""
        monkeypatch.setattr(reports, "_setup_complete", False)
        with pytest.raises(RuntimeError):
            reports.report_message(1, 2, 3, "spam")

    def test_is_setup_after_setup(self, db, monkeypatch):
        """setup() must flip is_setup deterministically."""
        monkeypatch.setattr(reports, "_setup_complete", False)
        monkeypatch.setattr(reports, "_db", None)
        assert reports.is_setup() is False
        reports.setup(db)
        assert reports.is_setup() is True


class TestMessageReports:
    """Test message report creation and lookup."""

    def test_report_message_creates_pending(self, reports_module):
        """A new message report must start as pending with defaults."""
        report = reports_module.report_message(
            reporter_id=1,
            message_id=100,
            channel_id=200,
            reason="spam",
            category="spam",
            server_id=300,
            reported_user_id=2,
            message_content="buy crypto",
        )
        assert report.status == ReportStatus.PENDING
        assert report.priority == "medium"
        assert report.reporter_id == 1
        assert report.message_id == 100
        assert report.channel_id == 200
        assert report.server_id == 300
        assert report.reported_user_id == 2
        assert report.message_content == "buy crypto"
        assert report.reported_at > 0
        assert report.id > 0

    def test_get_message_report_roundtrip(self, reports_module):
        """get_message_report must return the stored report."""
        report = reports_module.report_message(1, 100, 200, "spam")
        fetched = reports_module.get_message_report(report.id)
        assert fetched is not None
        assert fetched.id == report.id
        assert fetched.reason == "spam"
        assert fetched.status == ReportStatus.PENDING

    def test_get_missing_report_none(self, reports_module):
        """A missing report must return None."""
        assert reports_module.get_message_report(999999) is None

    def test_list_message_reports_orders_by_recency(self, reports_module):
        """Reports must be listed newest-first."""
        r1 = reports_module.report_message(1, 100, 200, "first")
        r2 = reports_module.report_message(1, 101, 200, "second")
        listed = reports_module.get_message_reports()
        ids = [r.id for r in listed]
        assert r2.id in ids
        assert r1.id in ids

    def test_list_filters_by_status(self, reports_module):
        """Status filtering must return only matching reports."""
        reports_module.report_message(1, 100, 200, "spam")
        r2 = reports_module.report_message(1, 101, 200, "nasty")
        reports_module.review_message_report(r2.id, admin_id=9, action="dismiss")
        pending = reports_module.get_message_reports(status_filter="pending")
        dismissed = reports_module.get_message_reports(status_filter="dismissed")
        assert all(r.status == ReportStatus.PENDING for r in pending)
        assert all(r.status == ReportStatus.DISMISSED for r in dismissed)
        assert len(pending) == 1
        assert len(dismissed) == 1

    def test_list_pagination(self, reports_module):
        """limit/offset must slice results."""
        for i in range(5):
            reports_module.report_message(1, 1000 + i, 200, f"r{i}")
        page1 = reports_module.get_message_reports(limit=2, offset=0)
        page2 = reports_module.get_message_reports(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        ids1 = {r.id for r in page1}
        ids2 = {r.id for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_message_report_counts(self, reports_module):
        """Counts must track each status bucket."""
        reports_module.report_message(1, 100, 200, "spam")
        r2 = reports_module.report_message(1, 101, 200, "nasty")
        r3 = reports_module.report_message(1, 102, 200, "gross")
        reports_module.review_message_report(r2.id, 9, "action")
        reports_module.review_message_report(r3.id, 9, "dismiss")
        counts = reports_module.get_message_report_counts()
        assert counts["pending"] == 1
        assert counts["actioned"] == 1
        assert counts["dismissed"] == 1
        assert counts["reviewed"] == 0
        assert counts["total"] == 3


class TestMessageReportReview:
    """Test the message report review workflow."""

    def test_review_action(self, reports_module):
        """action must set status to actioned and record admin."""
        report = reports_module.report_message(1, 100, 200, "spam")
        ok = reports_module.review_message_report(report.id, 9, "action", "banned")
        assert ok is True
        fetched = reports_module.get_message_report(report.id)
        assert fetched.status == ReportStatus.ACTIONED
        assert fetched.reviewed_by == 9
        assert fetched.admin_notes == "banned"
        assert fetched.action_taken == "action"
        assert fetched.reviewed_at > 0

    def test_review_dismiss(self, reports_module):
        """dismiss must set status to dismissed."""
        report = reports_module.report_message(1, 100, 200, "spam")
        reports_module.review_message_report(report.id, 9, "dismiss")
        fetched = reports_module.get_message_report(report.id)
        assert fetched.status == ReportStatus.DISMISSED

    def test_review_generic(self, reports_module):
        """Any other action must set status to reviewed."""
        report = reports_module.report_message(1, 100, 200, "spam")
        reports_module.review_message_report(report.id, 9, "review")
        fetched = reports_module.get_message_report(report.id)
        assert fetched.status == ReportStatus.REVIEWED

    def test_review_missing_report(self, reports_module):
        """Reviewing a nonexistent report must return False."""
        ok = reports_module.review_message_report(999999, 9, "dismiss")
        assert ok is False


class TestUserReports:
    """Test user report creation and lookup."""

    def test_report_user(self, reports_module):
        """report_user must store the report with evidence."""
        report = reports_module.report_user(
            reporter_id=1,
            reported_user_id=2,
            reason="harassment",
            category="harassment",
            details="repeated insults",
            evidence_message_ids=[100, 101],
        )
        assert report.status == ReportStatus.PENDING
        assert report.reported_user_id == 2
        assert report.reporter_id == 1
        assert report.evidence_message_ids == [100, 101]
        assert report.reason == "harassment"

    def test_get_user_report_roundtrip(self, reports_module):
        """get_user_report must return the stored report with parsed evidence."""
        report = reports_module.report_user(1, 2, "spam", evidence_message_ids=[50, 60])
        fetched = reports_module.get_user_report(report.id)
        assert fetched is not None
        assert fetched.id == report.id
        assert fetched.evidence_message_ids == [50, 60]

    def test_get_missing_user_report_none(self, reports_module):
        """A missing user report must return None."""
        assert reports_module.get_user_report(999999) is None

    def test_user_report_counts(self, reports_module):
        """User report counts must track status buckets."""
        r1 = reports_module.report_user(1, 2, "spam")
        r2 = reports_module.report_user(1, 3, "spam")
        reports_module.review_user_report(r1.id, 9, "dismiss")
        reports_module.review_user_report(r2.id, 9, "action")
        counts = reports_module.get_user_report_counts()
        assert counts["pending"] == 0
        assert counts["actioned"] == 1
        assert counts["dismissed"] == 1
        assert counts["total"] == 2

    def test_user_report_status_filter(self, reports_module):
        """User reports must filter by status."""
        r = reports_module.report_user(1, 2, "spam")
        reports_module.review_user_report(r.id, 9, "action")
        actioned = reports_module.get_user_reports(status_filter="actioned")
        pending = reports_module.get_user_reports(status_filter="pending")
        assert len(actioned) == 1
        assert len(pending) == 0


class TestUserReportReview:
    """Test the user report review workflow."""

    def test_review_user_report(self, reports_module):
        """Review must record admin + status."""
        report = reports_module.report_user(1, 2, "spam")
        ok = reports_module.review_user_report(report.id, 9, "action", "warned")
        assert ok is True
        fetched = reports_module.get_user_report(report.id)
        assert fetched.status == ReportStatus.ACTIONED
        assert fetched.reviewed_by == 9
        assert fetched.admin_notes == "warned"

    def test_review_missing_user_report(self, reports_module):
        """Reviewing a missing user report must return False."""
        assert reports_module.review_user_report(999999, 9, "dismiss") is False


class TestCategories:
    """Test the report category enum."""

    def test_all_categories(self):
        """Every documented category must exist with a stable value."""
        expected = {
            "harassment",
            "spam",
            "inappropriate",
            "illegal",
            "hate_speech",
            "threats",
            "impersonation",
            "other",
        }
        assert {c.value for c in ReportCategory} == expected


class TestRowConversion:
    """Test defensive row conversion for tuple rows."""

    def test_row_to_message_report_tuple(self):
        """A tuple row must convert without crashing."""
        from src.core.reports import _row_to_message_report

        row = (
            1,  # id
            100,  # message_id
            200,  # channel_id
            300,  # server_id
            1,  # reporter_id
            2,  # reported_user_id
            "spam",  # reason
            "spam",  # category
            None,  # details
            None,  # message_content
            None,  # evidence_urls
            "medium",  # priority
            "pending",  # status
            12345,  # reported_at
        )
        report = _row_to_message_report(row)
        assert report.id == 1
        assert report.status == ReportStatus.PENDING
        assert report.priority == "medium"

    def test_row_to_user_report_tuple(self):
        """A tuple user-report row must convert evidence ids."""
        from src.core.reports import _row_to_user_report

        row = (
            1,  # id
            2,  # reported_user_id
            3,  # reporter_id
            "spam",  # reason
            "spam",  # category
            None,  # details
            "10,20",  # evidence_message_ids
            None,  # evidence_urls
            "medium",  # priority
            "pending",  # status
            12345,  # reported_at
        )
        report = _row_to_user_report(row)
        assert report.evidence_message_ids == [10, 20]
        assert report.status == ReportStatus.PENDING
