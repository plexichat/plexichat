"""Tests for admin report moderation routes and dashboard wiring."""

import asyncio

from starlette.requests import Request

import src.api as api
import src.api.routes.admin.moderation as moderation_routes
import src.api.routes.admin.ui as ui_routes
from src.core import reports as reports_module
from src.api.schemas.admin import ModerationReportReviewRequest


ADMIN_HEADERS = {"Authorization": "Bearer admin-test-token"}


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"authorization", ADMIN_HEADERS["Authorization"].encode())],
            "query_string": b"",
        }
    )


def _setup_admin_reports(monkeypatch, modules):
    reports_module.setup(modules.db, modules.messaging)
    reports_module.create_tables(modules.db)
    monkeypatch.setattr(api, "_reports", reports_module, raising=False)
    monkeypatch.setattr(
        moderation_routes, "check_host_restriction", lambda request: None
    )
    monkeypatch.setattr(moderation_routes, "get_admin_from_token", lambda request: 1)


def test_message_report_admin_routes(
    monkeypatch, modules, test_user, second_test_user, test_server
):
    _setup_admin_reports(monkeypatch, modules)
    request = _request("/api/v1/admin/message-reports")

    report = reports_module.report_message(
        reporter_id=test_user["user"].id,
        message_id=987654321001,
        channel_id=test_server["channel"].id,
        server_id=test_server["server"].id,
        reported_user_id=second_test_user["user"].id,
        reason="Spam link",
        category="spam",
        details="Repeated phishing invite",
        message_content="join this fake giveaway",
    )

    rows = asyncio.run(
        moderation_routes.get_message_reports(
            request, status_filter=None, limit=50, offset=0
        )
    )
    assert any(item.id == str(report.id) for item in rows)

    counts = asyncio.run(moderation_routes.get_message_report_counts(request))
    assert counts.pending >= 1

    review = asyncio.run(
        moderation_routes.review_message_report(
            report.id,
            ModerationReportReviewRequest(
                action="action", notes="Escalated to moderation"
            ),
            request,
        )
    )
    assert review.action == "action"

    actioned_rows = asyncio.run(
        moderation_routes.get_message_reports(
            request, status_filter="actioned", limit=50, offset=0
        )
    )
    assert any(item.id == str(report.id) for item in actioned_rows)


def test_user_report_admin_routes(monkeypatch, modules, test_user):
    _setup_admin_reports(monkeypatch, modules)
    request = _request("/api/v1/admin/user-reports")

    report = reports_module.report_user(
        reporter_id=test_user["user"].id,
        reported_user_id=777000123456,
        reason="Harassment",
        category="harassment",
        details="Repeated abusive DMs",
        evidence_message_ids=[111, 222],
    )

    rows = asyncio.run(
        moderation_routes.get_user_reports(
            request, status_filter=None, limit=50, offset=0
        )
    )
    assert any(item.id == str(report.id) for item in rows)

    counts = asyncio.run(moderation_routes.get_user_report_counts(request))
    assert counts.pending >= 1

    review = asyncio.run(
        moderation_routes.review_user_report(
            report.id,
            ModerationReportReviewRequest(
                action="dismiss", notes="Insufficient evidence"
            ),
            request,
        )
    )
    assert review.action == "dismiss"

    dismissed_rows = asyncio.run(
        moderation_routes.get_user_reports(
            request, status_filter="dismissed", limit=50, offset=0
        )
    )
    assert any(item.id == str(report.id) for item in dismissed_rows)


def test_admin_dashboard_includes_report_sections(monkeypatch):
    monkeypatch.setattr(ui_routes, "check_host_restriction", lambda request: None)

    response = asyncio.run(
        ui_routes.admin_dashboard_page(_request("/api/v1/admin/ui-dashboard"))
    )
    assert response.status_code == 200
    html = response.body.decode()
    assert "Message Reports" in html
    assert "User Reports" in html
    assert "message-reports-tbody" in html
    assert "user-reports-tbody" in html
