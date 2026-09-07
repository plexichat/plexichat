"""Telemetry route contract tests.

M1: the telemetry schema silently ignored unknown fields (pydantic's
default ``extra='ignore'``), so a client typo such as ``usr_id`` returned
200 while the entry was stored empty — silent data loss. The schema now
forbids extras, so contract drift surfaces as an explicit 422.
"""


def _valid_entry(**overrides):
    entry = {
        "endpoint": "/users/@me",
        "method": "GET",
        "response_time_ms": 12,
        "status_code": 200,
    }
    entry.update(overrides)
    return entry


def test_telemetry_unknown_entry_field_rejected(test_client):
    # The app maps request-validation failures to 400 (docs/errors.md); the
    # point is the unknown field is rejected, not silently dropped with 200.
    response = test_client.post(
        "/api/v1/telemetry/response-times",
        json={"entries": [_valid_entry(usr_id=1)]},
    )
    assert response.status_code == 400


def test_telemetry_unknown_top_level_field_rejected(test_client):
    response = test_client.post(
        "/api/v1/telemetry/response-times",
        json={"entries": [_valid_entry()], "bogus": True},
    )
    assert response.status_code == 400


def test_telemetry_valid_payload_not_rejected_by_schema(test_client):
    # Whether the entry is accepted or dropped (module disabled) is a runtime
    # concern; the schema itself must never reject the documented payload.
    response = test_client.post(
        "/api/v1/telemetry/response-times",
        json={"entries": [_valid_entry(timestamp=1_700_000_000_000)]},
    )
    assert response.status_code == 200
