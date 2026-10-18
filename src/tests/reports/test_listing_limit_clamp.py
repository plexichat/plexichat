"""Regression tests: admin report listings must clamp ``limit`` from below.

``get_message_reports`` / ``get_user_reports`` in ``src.core.reports``
forwarded the caller's ``limit`` straight into SQL ``LIMIT ?``. SQLite reads
``LIMIT -1`` as *unlimited*, so
``GET /admin/moderation/message-reports?limit=-1`` (and the user-report
listing) returned *every* row instead of an empty page. Both helpers now clamp
``limit`` to ``[0, MAX_PAGE_SIZE]`` and ``offset`` to ``>= 0``.
"""

import pytest

from src.core import reports


@pytest.fixture
def reports_module(db):
    """Reports module bound to the test db."""
    reports.setup(db)
    return reports


def _seed(reports_module):
    """Create two message reports and two user reports."""
    for message_id in (100, 101):
        reports_module.report_message(
            reporter_id=1,
            message_id=message_id,
            channel_id=200,
            reason="spam",
            category="spam",
            server_id=300,
            reported_user_id=2,
            message_content="buy crypto",
        )
    for reported_user_id in (2, 3):
        reports_module.report_user(1, reported_user_id, "spam")


class TestReportListingNegativeLimitClamp:
    def test_message_reports_negative_limit_is_empty(self, reports_module):
        _seed(reports_module)

        assert len(reports_module.get_message_reports(limit=10)) == 2
        assert reports_module.get_message_reports(limit=-1) == []
        assert reports_module.get_message_reports(limit=0) == []

    def test_user_reports_negative_limit_is_empty(self, reports_module):
        _seed(reports_module)

        assert len(reports_module.get_user_reports(limit=10)) == 2
        assert reports_module.get_user_reports(limit=-1) == []
        assert reports_module.get_user_reports(limit=0) == []

    def test_message_reports_limit_is_capped(self, reports_module):
        _seed(reports_module)

        # Clamped on both sides: a huge limit still returns the real rows
        # rather than erroring or going negative.
        assert len(reports_module.get_message_reports(limit=10**6)) == 2

    def test_negative_offset_is_clamped(self, reports_module):
        _seed(reports_module)

        # A negative offset must behave like offset=0, not skip rows.
        assert len(reports_module.get_message_reports(limit=10, offset=-5)) == 2
        assert len(reports_module.get_user_reports(limit=10, offset=-5)) == 2
