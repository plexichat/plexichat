"""Regression tests for inbound federation payload boundary hardening.

A valid PlexiJoin HMAC proves *which* peer sent a payload, not that every value
in it is safe to relay or copy into an application log line. Two gaps were
closed here:

* ``_bound_payload_value`` accepted non-finite floats. Python's ``json.loads``
  happily produces ``NaN`` / ``Infinity`` from non-standard literals (and a
  huge exponent such as ``1e400`` parses to ``inf``), so a signed envelope
  could smuggle them past the integer bound, into relayed ops and logs, where
  re-serialisation emits bare ``NaN``/``Infinity`` tokens that strict JSON
  parsers reject -- a parser differential against the client.

* ``artifact_event_inbound`` copied ``event_type`` verbatim into a log line
  while its sibling routes bind ``message_type`` / ``op_type`` to a
  restrictive token grammar. A control character in ``event_type`` could
  forge a log entry in any handler that does not scrub, and an oversized
  value could bloat log storage.
"""

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes import federation as mod


# --- non-finite float rejection -------------------------------------------


def test_bound_payload_value_rejects_nan_literal():
    """The non-standard JSON ``NaN`` literal must not pass the boundary."""
    payload = json.loads('{"score": NaN}')
    with pytest.raises(HTTPException) as exc:
        mod._bound_payload_value(payload)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "1e400", "-1e400"])
def test_bound_payload_value_rejects_infinity_literals(literal):
    """Infinity (including via a huge exponent) is not a bounded number."""
    payload = json.loads(f'{{"score": {literal}}}')
    with pytest.raises(HTTPException) as exc:
        mod._bound_payload_value(payload)
    assert exc.value.status_code == 400


def test_bound_payload_value_rejects_nested_nonfinite():
    """Non-finite values are rejected at any nesting depth."""
    payload = {"op": {"cells": [{"v": 1.0}, {"v": float("nan")}]}}
    with pytest.raises(HTTPException) as exc:
        mod._bound_payload_value(payload)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("value", [1.0, -0.5, 3.14159, 1e10, 0.0])
def test_bound_payload_value_accepts_finite_floats(value):
    """Ordinary finite floats remain valid (no over-correction)."""
    mod._bound_payload_value({"v": value})


# --- event_type grammar binding -------------------------------------------


def _request(body: bytes) -> Request:
    return cast(
        Request,
        SimpleNamespace(
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=body),
        ),
    )


def _event_payload(**overrides):
    payload = {"event_type": "artifact.updated", "artifact": {"id": "abc"}}
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_event_type",
    [
        "evt\nFORGED admin session",  # log-line forging via newline
        "evt\rFORGED",
        "evt\x1b[31m",  # ANSI escape
        "",  # empty token
        "a" * 200,  # above the 128-char grammar bound
        "has space",  # not in the restrictive token class
        123,  # non-string
    ],
)
async def test_artifact_event_rejects_unbounded_event_type(bad_event_type):
    """A signed envelope cannot smuggle an unsafe token into the log line."""
    request = _request(json.dumps(_event_payload(event_type=bad_event_type)).encode())
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.artifact_event_inbound(request)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_artifact_event_accepts_valid_event_type():
    """Well-formed event types are still acknowledged."""
    request = _request(json.dumps(_event_payload()).encode())
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
    ):
        result = await mod.artifact_event_inbound(request)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_artifact_event_strict_json_rejects_nan():
    """A NaN literal is rejected before any logging/relay on the event path."""
    body = b'{"event_type": "artifact.updated", "extra": NaN}'
    request = _request(body)
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.artifact_event_inbound(request)
    assert exc.value.status_code == 400
