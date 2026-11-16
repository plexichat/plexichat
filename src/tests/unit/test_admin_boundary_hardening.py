"""Regression tests for admin-route request/response boundary caps.

Each test pins one guard added during the admin hardening sweep: numeric query
windows, filter-string lengths, export row caps, static-asset headers and
scoped permission gates. The guards live in the route modules; these tests read
the route signatures/constants so a future edit that drops a bound fails loudly.
"""

import inspect

from fastapi.params import Query as QueryParam

import src.api.routes.admin.audit as audit_routes
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
