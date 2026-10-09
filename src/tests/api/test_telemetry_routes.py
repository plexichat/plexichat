"""Telemetry route contract tests.

M1: the telemetry schema silently ignored unknown fields (pydantic's
default ``extra='ignore'``), so a client typo such as ``usr_id`` returned
200 while the entry was stored empty — silent data loss. The schema now
forbids extras, so contract drift surfaces as an explicit 422.

The rate-limit key is also pinned here: the handler must key on the
trusted-proxy-aware client IP (``src.utils.net.get_client_ip``) rather than
the raw socket peer. Behind a reverse proxy the raw peer is the proxy, so
using it collapsed every client into one shared telemetry bucket and let a
single client 429 the whole deployment.
"""

import asyncio

import pytest
from fastapi import HTTPException, Request

import utils.config as config
from src.api.routes import telemetry as telemetry_mod
from src.api.schemas.telemetry import ResponseTimeEntry, TelemetrySubmission


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


# ---------------------------------------------------------------------------
# Rate-limit bucket keying (trusted proxy aware)
# ---------------------------------------------------------------------------


def _make_request(client_host, x_forwarded_for=None):
    headers = []
    if x_forwarded_for is not None:
        headers.append((b"x-forwarded-for", x_forwarded_for.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/telemetry/response-times",
        "headers": headers,
        "client": (client_host, 50000) if client_host else None,
    }
    return Request(scope)


def _submission():
    return TelemetrySubmission(
        entries=[
            ResponseTimeEntry(
                endpoint="/users/@me",
                method="GET",
                response_time_ms=12,
                status_code=200,
            )
        ]
    )


def _captured_rate_limit_ip(request):
    """Drive the handler with the rate limiter stubbed out and return the IP
    the handler keyed the bucket on.

    The stub reports the request denied, which short-circuits the handler with
    a 429 *before* it reaches the telemetry module, so this exercises only the
    key-derivation logic under test.
    """
    captured = {}

    class _Denied:
        allowed = False

    async def _fake_acheck(**kwargs):
        # The handler awaits ``acheck_rate_limit``; the stub must be a
        # coroutine, not a plain function returning the result.
        captured.update(kwargs)
        return _Denied()

    rl_mod = telemetry_mod.ratelimit
    original = rl_mod.acheck_rate_limit
    rl_mod.acheck_rate_limit = _fake_acheck
    try:
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(
                telemetry_mod.submit_response_times(
                    _submission(), request, current_user=None
                )
            )
        assert excinfo.value.status_code == 429
    finally:
        rl_mod.acheck_rate_limit = original
    return captured.get("ip_address")


def test_telemetry_ratelimit_key_is_proxy_aware():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    first = _captured_rate_limit_ip(_make_request("10.0.0.1", "203.0.113.7"))
    second = _captured_rate_limit_ip(_make_request("10.0.0.1", "198.51.100.9"))
    # Each forwarded client resolves to its own IP...
    assert first == "203.0.113.7"
    assert second == "198.51.100.9"
    # ...so two clients behind the same proxy do not share a bucket.
    assert first != second


def test_telemetry_ratelimit_ignores_xff_from_untrusted_peer():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    # Direct peer is not a trusted proxy, so a spoofed XFF is ignored and the
    # bucket stays keyed on the address we actually received from.
    assert (
        _captured_rate_limit_ip(_make_request("203.0.113.9", "198.51.100.7"))
        == "203.0.113.9"
    )
