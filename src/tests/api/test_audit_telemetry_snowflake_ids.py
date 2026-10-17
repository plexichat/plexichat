"""Ratchet: audit-log and telemetry responses never carry numeric snowflakes.

Both surfaces sit outside the OpenAPI schema walk (the audit list is an
unmodelled dict; telemetry returns aggregate stats), so a 64-bit id could be
serialized as a JSON number and rounded by the admin UI past 2**53 without any
other gate noticing. This drives the real routes with rows whose ids exceed
2**53 and scans the JSON for any integer that a JS ``Number`` cannot hold.
"""

import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# 2**53 + 1: the first integer a JS ``Number`` cannot represent.
BIG = 9007199254740993


def _assert_no_large_ints(payload, *, path="$"):
    """Assert no integer >= 2**53 appears anywhere in a JSON payload."""
    offenders = []

    def walk(node, where):
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            if abs(node) >= 2**53:
                offenders.append(f"{where} -> {node}")
            return
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")

    walk(payload, path)
    assert not offenders, (
        "numeric snowflake id(s) would be rounded by a JS client:\n"
        + "\n".join(offenders)
    )


def test_assert_no_large_ints_flags_and_accepts() -> None:
    _assert_no_large_ints({"a": 1, "b": "9007199254740993", "c": [1, 2]})
    try:
        _assert_no_large_ints({"id": BIG})
    except AssertionError:
        pass
    else:  # pragma: no cover - the guard must fail loudly
        raise AssertionError("scanner failed to flag a numeric snowflake")


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def fetch_all(self, query, params=None):
        return self._rows

    def fetch_one(self, query, params=None):
        return {"total": len(self._rows)}


def _audit_row():
    return {
        "id": 7,  # rowid: stays numeric
        "admin_id": BIG,
        "action": "user_ban",
        "target_type": "user",
        "target_id": BIG + 1,
        "target_user_id": BIG + 2,
        "details": "{}",
        "ip_address": "1.2.3.4",
        "user_agent": "ua",
        "status": "success",
        "created_at": 1700000000000,  # millis; below 2**53, stays numeric
    }


def _audit_client():
    from src.api.routes.admin import audit as audit_routes

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission", return_value=1),
        patch("src.api.get_db", return_value=_FakeDB([_audit_row()])),
    )
    for patcher in patchers:
        patcher.start()
    return client, patchers


def _stop(patchers):
    for patcher in reversed(patchers):
        patcher.stop()


def test_audit_logs_stringify_snowflake_ids() -> None:
    client, patchers = _audit_client()
    try:
        response = client.get("/api/v1/admin/audit/logs")
        assert response.status_code == 200, response.text
        body = response.json()
        entry = body["logs"][0]
        assert entry["admin_id"] == str(BIG)
        assert isinstance(entry["admin_id"], str)
        assert entry["target_id"] == str(BIG + 1)
        assert isinstance(entry["target_id"], str)
        # Rowid id and millisecond timestamp stay numeric.
        assert entry["id"] == 7
        assert entry["created_at"] == 1700000000000
        _assert_no_large_ints(body)
    finally:
        _stop(patchers)


def test_audit_export_json_stringifies_snowflake_ids() -> None:
    client, patchers = _audit_client()
    try:
        response = client.get("/api/v1/admin/audit/logs/export?format=json")
        assert response.status_code == 200, response.text
        body = response.json()
        entry = body["logs"][0]
        assert entry["admin_id"] == str(BIG)
        assert entry["target_id"] == str(BIG + 1)
        assert entry["target_user_id"] == str(BIG + 2)
        assert isinstance(entry["target_user_id"], str)
        _assert_no_large_ints(body)
    finally:
        _stop(patchers)


def _telemetry_client():
    from src.api.routes.admin import telemetry as telemetry_routes

    app = FastAPI()
    app.include_router(telemetry_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    patchers = (
        patch.object(telemetry_routes, "check_host_restriction"),
        patch.object(telemetry_routes, "get_admin_from_token", return_value=1),
    )
    for patcher in patchers:
        patcher.start()
    return client, patchers


def _endpoint_stat():
    from src.core.telemetry import EndpointStats

    return EndpointStats(
        endpoint="/api/v1/channels/{channel_id}",
        method="GET",
        count=9,
        avg_response_time_ms=12.5,
        min_response_time_ms=3.0,
        max_response_time_ms=40.0,
        p50_response_time_ms=10.0,
        p95_response_time_ms=30.0,
        p99_response_time_ms=40.0,
        error_rate=0.0,
        last_updated=1700000000000,
        avg_queries=2.0,
        avg_query_time_ms=1.5,
        error_count=0,
    )


def test_telemetry_stats_have_no_numeric_snowflakes() -> None:
    from src.core import telemetry as telemetry_core

    client, patchers = _telemetry_client()
    try:
        with (
            patch.object(telemetry_core, "is_setup", return_value=True),
            patch.object(
                telemetry_core, "get_endpoint_stats", return_value=[_endpoint_stat()]
            ),
        ):
            response = client.get("/api/v1/admin/telemetry/stats")
            assert response.status_code == 200, response.text
            _assert_no_large_ints(response.json())

            export = client.get("/api/v1/admin/telemetry/export?format=json")
            assert export.status_code == 200, export.text
            _assert_no_large_ints(json.loads(export.text))
    finally:
        _stop(patchers)


def test_telemetry_history_has_no_numeric_snowflakes() -> None:
    from src.core import telemetry as telemetry_core

    client, patchers = _telemetry_client()
    try:
        history = [
            {
                "timestamp": 1700000000000,
                "avg_ms": 12.5,
                "count": 3,
                "error_count": 0,
            }
        ]
        with (
            patch.object(telemetry_core, "is_setup", return_value=True),
            patch.object(
                telemetry_core, "get_response_time_history", return_value=history
            ),
        ):
            response = client.get("/api/v1/admin/telemetry/history?endpoint=/x")
            assert response.status_code == 200, response.text
            _assert_no_large_ints(response.json())
    finally:
        _stop(patchers)
