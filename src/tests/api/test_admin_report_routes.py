"""Tests for admin report moderation routes and dashboard wiring."""

import pytest


@pytest.fixture
def isolated_reports(db, monkeypatch):
    """Point the reports module at the per-test database.

    ``src.core.reports`` keeps a module-global db handle; re-point it for
    this test's database and restore the previous global state afterwards so
    nothing leaks across tests in the same worker.
    """
    from src.core import reports

    previous_db = getattr(reports, "_db", None)
    previous_setup = getattr(reports, "_setup_complete", False)

    reports.setup(db)

    yield reports

    monkeypatch.setattr(reports, "_db", previous_db, raising=False)
    monkeypatch.setattr(reports, "_setup_complete", previous_setup, raising=False)


def test_admin_dashboard_includes_report_sections():
    """The admin moderation router exposes report review endpoints."""
    from src.api.routes.admin.moderation import router as moderation_router

    paths = [getattr(route, "path", "") for route in moderation_router.routes]
    assert any("report" in path for path in paths)


def test_message_report_admin_routes(isolated_reports, messaging_manager, two_users):
    """A filed message report is retrievable for admin review."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    msg = messaging_manager.send_message(user1.id, dm.id, "Reportable content")

    report = isolated_reports.report_message(
        reporter_id=user2.id,
        message_id=msg.id,
        channel_id=dm.id,
        reason="spam",
        category="spam",
        server_id=None,
        reported_user_id=user1.id,
        message_content=msg.content,
    )
    assert report is not None

    fetched = isolated_reports.get_message_report(report.id)
    assert fetched is not None
    assert fetched.message_id == msg.id
    assert fetched.reporter_id == user2.id
    status = (
        fetched.status.value if hasattr(fetched.status, "value") else fetched.status
    )
    assert status == "pending"

    listed = isolated_reports.get_message_reports()
    assert any(r.id == report.id for r in listed)


def test_user_report_admin_routes(isolated_reports, two_users):
    """A filed user report is retrievable for admin review."""
    user1, user2 = two_users

    report = isolated_reports.report_user(
        reporter_id=user1.id,
        reported_user_id=user2.id,
        reason="harassment",
        category="harassment",
    )
    assert report is not None

    fetched = isolated_reports.get_user_report(report.id)
    assert fetched is not None
    assert fetched.reporter_id == user1.id
    assert fetched.reported_user_id == user2.id
    status = (
        fetched.status.value if hasattr(fetched.status, "value") else fetched.status
    )
    assert status == "pending"
