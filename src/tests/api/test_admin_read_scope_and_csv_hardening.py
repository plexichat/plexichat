"""Regression: scoped admin telemetry reads + safe admin CSV exports.

Two weaknesses are pinned here:

1. The telemetry *read* endpoints authenticated with ``get_admin_from_token``
   (session validation only) while the destructive reset endpoint checked a
   permission scope. Any admin holding a valid session -- including a
   low-privilege read-only role -- could therefore dump system-wide endpoint
   paths, latencies, query counts and error rates. Reads now require the
   documented ``metrics.read`` scope.

2. The audit and telemetry CSV exports wrote untrusted free text (audit
   details/actions/user agents and stored telemetry endpoint paths) verbatim.
   Spreadsheets evaluate a cell starting with ``=``, ``+``, ``-`` or ``@`` as a
   formula, so a crafted log value could execute on the admin's machine when
   the downloaded CSV was opened. Every text cell is now rendered as a literal.
"""

import csv
import inspect
import io
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.routes.admin import audit as audit_routes
from src.api.routes.admin import telemetry as telemetry_routes


# ---------------------------------------------------------------------------
# Telemetry read scope
# ---------------------------------------------------------------------------


def _telemetry_client():
    app = FastAPI()
    app.include_router(telemetry_routes.router, prefix="/api/v1/admin")
    return TestClient(app)


_READ_PATHS = (
    "/api/v1/admin/telemetry/stats",
    "/api/v1/admin/telemetry/history?endpoint=/x",
    "/api/v1/admin/telemetry/export?format=json",
)


def test_telemetry_reads_require_a_permission_scope():
    """An authenticated admin without the scope gets 403 on every read."""
    client = _telemetry_client()
    seen_scopes = []

    def _deny(request, scope):
        seen_scopes.append(scope)
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    with (
        patch.object(telemetry_routes, "check_host_restriction"),
        patch.object(telemetry_routes, "require_admin_permission", side_effect=_deny),
    ):
        for path in _READ_PATHS:
            response = client.get(path)
            assert response.status_code == 403, (path, response.text)

    assert len(seen_scopes) == len(_READ_PATHS)
    assert all(scope == "metrics.read" for scope in seen_scopes), seen_scopes


def test_telemetry_reads_succeed_for_scoped_admin():
    from src.core import telemetry as telemetry_core

    client = _telemetry_client()
    with (
        patch.object(telemetry_routes, "check_host_restriction"),
        patch.object(telemetry_routes, "require_admin_permission", return_value=1),
        patch.object(telemetry_core, "is_setup", return_value=False),
    ):
        for path in _READ_PATHS:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)


def test_telemetry_read_handlers_no_longer_authenticate_only():
    """Structural guard: the read handlers must gate on a concrete scope."""
    for handler in (
        telemetry_routes.get_telemetry_stats,
        telemetry_routes.get_telemetry_history,
        telemetry_routes.export_telemetry_stats,
    ):
        source = inspect.getsource(handler)
        assert "require_admin_permission(" in source, handler.__name__
        assert "get_admin_from_token(" not in source, handler.__name__


# ---------------------------------------------------------------------------
# CSV formula-injection neutralization
# ---------------------------------------------------------------------------


def test_csv_cell_helpers_quote_formula_leads():
    """Both exporters treat every dangerous leading character as text."""
    for module in (audit_routes, telemetry_routes):
        for lead in ("=", "+", "-", "@", "\t", "\r", "  =cmd|'/c calc'!A0"):
            assert module._csv_cell(lead + "x").startswith("'"), (
                module.__name__,
                lead,
            )
        assert module._csv_cell("ordinary value") == "ordinary value"
        assert module._csv_cell(None) == ""


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, query, params=None):
        return self._rows

    def fetch_one(self, query, params=None):
        return {"total": len(self._rows)}


def _audit_client(rows):
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=_FakeDB(rows)),
    )
    for patcher in patchers:
        patcher.start()
    return client, patchers


def test_audit_csv_export_never_emits_formula_cells():
    row = {
        "id": 7,
        "admin_id": 123,
        "action": '=HYPERLINK("http://evil.example","click")',
        "target_type": "@SUM(1+1)",
        "target_id": 999,
        "details": "+cmd|'/c calc'!A0",
        "ip_address": "-1+1",
        "user_agent": "=1+1",
        "status": "success",
        "created_at": 1700000000000,
    }
    client, patchers = _audit_client([row])
    try:
        response = client.get("/api/v1/admin/audit/logs/export?format=csv")
        assert response.status_code == 200, response.text
        rows = list(csv.reader(io.StringIO(response.text)))
        assert len(rows) == 2  # header + one data row
        data_cells = rows[1]
        for cell in data_cells:
            probe = cell.lstrip(" \t\r\n")
            assert not (probe and probe[0] in ("=", "+", "-", "@", "\t", "\r")), (
                data_cells
            )
        assert any(cell.startswith("'=") for cell in data_cells), data_cells
        # Redaction still runs on the same cells (no weakening of that guard).
        assert audit_routes.redact_secrets is not None
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
