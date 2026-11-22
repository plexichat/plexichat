"""Telemetry numeric-boundary regression.

The response-time submission sink is reachable without authentication. Its
schema accepted non-finite floats and unbounded integers: pydantic allows
``NaN``/``Infinity`` by default, and Python ints are arbitrary precision.
``NaN`` defeats every range check in the persistence layer (both ``x < 0``
and ``x > max`` are False for NaN), so it reached the ``REAL`` metric columns
and turned later ``AVG()`` aggregates into NaN; ``Infinity`` and huge ints
overflowed stored metrics/query bounds. Reject all of it at the request
boundary without rejecting legitimate samples.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.telemetry import ResponseTimeEntry


def _entry(**overrides):
    data = {
        "endpoint": "/users/@me",
        "method": "GET",
        "response_time_ms": 12.5,
        "status_code": 200,
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("field", ["response_time_ms", "db_time_ms"])
@pytest.mark.parametrize(
    "bad",
    ["NaN", "Infinity", "-Infinity", float("nan"), float("inf"), float("-inf")],
)
def test_non_finite_metrics_rejected(field, bad):
    with pytest.raises(ValidationError):
        ResponseTimeEntry(**_entry(**{field: bad}))


@pytest.mark.parametrize("field", ["db_queries", "timestamp"])
@pytest.mark.parametrize("bad", [10**100, -1, -(10**30)])
def test_unbounded_or_negative_integer_fields_rejected(field, bad):
    with pytest.raises(ValidationError):
        ResponseTimeEntry(**_entry(**{field: bad}))


def test_response_time_above_core_ceiling_rejected():
    with pytest.raises(ValidationError):
        ResponseTimeEntry(**_entry(response_time_ms=24 * 60 * 60 * 1000 + 1))


def test_legitimate_sample_still_validated():
    entry = ResponseTimeEntry(
        **_entry(db_queries=7, db_time_ms=3.5, timestamp=1_700_000_000_000)
    )
    assert entry.response_time_ms == 12.5
    assert entry.db_queries == 7
    assert entry.db_time_ms == 3.5
    assert entry.timestamp == 1_700_000_000_000


def test_route_rejects_non_finite_metric(test_client):
    import json

    body = json.dumps(
        {"entries": [_entry(db_time_ms=float("nan"))]},
        allow_nan=True,
    )
    resp = test_client.post(
        "/api/v1/telemetry/response-times",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code >= 400
