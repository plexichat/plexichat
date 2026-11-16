"""Regression tests for admin-route request/response boundary caps.

Each test pins one guard added during the admin hardening sweep: numeric query
windows, filter-string lengths, export row caps, static-asset headers and
scoped permission gates. The guards live in the route modules; these tests read
the route signatures/constants so a future edit that drops a bound fails loudly.
"""

import asyncio
import inspect

from fastapi.params import Query as QueryParam
from starlette.requests import Request

import src.api.routes.admin.audit as audit_routes
import src.api.routes.admin.ui as ui_routes
import src.api.routes.admin.security as security_routes
import src.api.routes.admin.moderation as moderation_routes
import src.api.routes.admin.tickets as tickets_routes
import src.api.routes.admin.dashboard as dashboard_routes
import src.api.routes.admin.telemetry as telemetry_routes


def _query_param(func, name):
    default = inspect.signature(func).parameters[name].default
    assert isinstance(default, QueryParam), f"{name} is not a Query parameter"
    return default


def _constraint(func, name, kind):
    """Return a pydantic constraint (Ge/Le/MaxLen) on a Query parameter."""
    for meta in getattr(_query_param(func, name), "metadata", ()):
        if type(meta).__name__ == kind:
            return meta
    raise AssertionError(f"{name} has no {kind} constraint")


class TestTelemetryWindowBounds:
    def test_stats_window_and_filters_bounded(self):
        hours = _constraint(telemetry_routes.get_telemetry_stats, "hours", "Le")
        assert hours.le == 8760
        assert _constraint(telemetry_routes.get_telemetry_stats, "hours", "Ge").ge == 1
        assert (
            _constraint(
                telemetry_routes.get_telemetry_stats, "endpoint", "MaxLen"
            ).max_length
            == 256
        )
        assert (
            _constraint(
                telemetry_routes.get_telemetry_stats, "source", "MaxLen"
            ).max_length
            == 64
        )

    def test_history_window_and_bucket_bounded(self):
        assert (
            _constraint(telemetry_routes.get_telemetry_history, "hours", "Le").le
            == 8760
        )
        bucket = _constraint(
            telemetry_routes.get_telemetry_history, "bucket_minutes", "Le"
        )
        assert bucket.le == 1440
        assert (
            _constraint(
                telemetry_routes.get_telemetry_history, "bucket_minutes", "Ge"
            ).ge
            == 1
        )
        assert (
            _constraint(
                telemetry_routes.get_telemetry_history, "endpoint", "MaxLen"
            ).max_length
            == 256
        )

    def test_export_window_bounded(self):
        assert (
            _constraint(telemetry_routes.export_telemetry_stats, "hours", "Le").le
            == 8760
        )


class TestSystemAlertBounds:
    def test_alerts_limit_and_window_bounded(self):
        assert _constraint(dashboard_routes.get_system_alerts, "limit", "Le").le == 200
        assert _constraint(dashboard_routes.get_system_alerts, "hours", "Le").le == 8760
        assert (
            _constraint(
                dashboard_routes.get_system_alerts, "source", "MaxLen"
            ).max_length
            == 64
        )
        assert (
            _constraint(
                dashboard_routes.get_system_alerts, "severity", "MaxLen"
            ).max_length
            == 32
        )

    def test_alert_stats_window_bounded(self):
        assert (
            _constraint(dashboard_routes.get_system_alert_stats, "hours", "Le").le
            == 8760
        )


class TestTicketQueueBounds:
    def test_pagination_and_filter_bounded(self):
        assert _constraint(tickets_routes.get_tickets, "limit", "Le").le == 200
        assert _constraint(tickets_routes.get_tickets, "offset", "Le").le == 1_000_000
        assert (
            _constraint(
                tickets_routes.get_tickets, "status_filter", "MaxLen"
            ).max_length
            == 32
        )


class TestModerationFilterBounds:
    def test_all_report_status_filters_bounded(self):
        for handler in (
            moderation_routes.get_hash_reports,
            moderation_routes.get_message_reports,
            moderation_routes.get_user_reports,
        ):
            assert _constraint(handler, "status_filter", "MaxLen").max_length == 32


class TestAccessTokenDetailBounds:
    def test_recent_event_window_bounded(self):
        assert (
            _constraint(
                security_routes.get_access_token_detail, "recent_limit", "Le"
            ).le
            == 1000
        )
        assert (
            _constraint(
                security_routes.get_access_token_detail, "recent_limit", "Ge"
            ).ge
            == 1
        )


class TestStaticResponseHeaders:
    def test_favicon_sets_nosniff(self, tmp_path, monkeypatch):
        icon = tmp_path / "favicon.svg"
        icon.write_text(
            "<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8"
        )
        monkeypatch.setattr(ui_routes, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(ui_routes, "_admin_static_path", lambda *parts: icon)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/static/favicon.svg",
                "headers": [(b"host", b"testserver")],
                "client": ("127.0.0.1", 12345),
                "scheme": "http",
                "server": ("testserver", 80),
                "query_string": b"",
            }
        )
        response = asyncio.run(ui_routes.admin_favicon(request))
        assert response.headers["X-Content-Type-Options"] == "nosniff"


class TestAuditExportBounds:
    def test_export_caps_rows_and_filter_lengths(self):
        # The export must carry an explicit row cap, not stream the whole table.
        assert audit_routes._MAX_AUDIT_EXPORT_ROWS > 0
        source = inspect.getsource(audit_routes.export_audit_logs)
        assert "LIMIT ?" in source
        assert "_MAX_AUDIT_EXPORT_ROWS" in source

        assert (
            _constraint(
                audit_routes.export_audit_logs, "action_family", "MaxLen"
            ).max_length
            == 64
        )
        assert (
            _constraint(audit_routes.export_audit_logs, "status", "MaxLen").max_length
            == 32
        )
        assert (
            _constraint(
                audit_routes.export_audit_logs, "target_type", "MaxLen"
            ).max_length
            == 64
        )
