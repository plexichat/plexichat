"""Deep hardening: the gateway handshake rejects cross-site origins."""

from types import SimpleNamespace

import pytest

import utils.config as config
from src.api.websocket.gateway import gateway_endpoint
from src.api.websocket.handshake import (
    HandshakeRejected,
    origin_allowed,
    validate_handshake,
)

ALLOW = {"cors_origins": ["https://app.plexichat.com", "https://plexichat.com/"]}


def _config(websocket=None):
    def get(key, default=None):
        if key == "websocket":
            return websocket or {}
        if key == "api":
            return ALLOW
        return default

    return get


class FakeWebSocket:
    def __init__(self, headers=None, query=None, host="203.0.113.9"):
        self.headers = headers or {}
        self.query_params = query or {}
        self.client = SimpleNamespace(host=host)
        self.accepted = False
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


def test_origin_allowed_rules(monkeypatch):
    monkeypatch.setattr(config, "get", _config())
    assert origin_allowed("https://app.plexichat.com") is True
    assert origin_allowed("https://app.plexichat.com/") is True
    assert origin_allowed("https://evil.example") is False
    assert origin_allowed(None) is True  # non-browser client
    assert origin_allowed("not an origin") is False


def test_wildcard_allowlist_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "get", _config(websocket={"allowed_origins": ["*"]}))
    assert origin_allowed("https://evil.example") is False


def test_forged_origin_raises(monkeypatch):
    monkeypatch.setattr(config, "get", _config())
    with pytest.raises(HandshakeRejected):
        validate_handshake(FakeWebSocket({"origin": "https://evil.example"}))


def test_allowed_origin_and_missing_origin_pass(monkeypatch):
    monkeypatch.setattr(config, "get", _config())
    validate_handshake(FakeWebSocket({"Origin": "https://app.plexichat.com"}))
    validate_handshake(FakeWebSocket({}))


def test_selftest_bypasses_origin_gate(monkeypatch):
    monkeypatch.setattr(config, "get", _config())
    validate_handshake(
        FakeWebSocket({"origin": "https://evil.example"}), is_selftest=True
    )


def test_malformed_handshake_token_rejected(monkeypatch):
    monkeypatch.setattr(config, "get", _config())
    with pytest.raises(HandshakeRejected):
        validate_handshake(
            FakeWebSocket(
                {"origin": "https://app.plexichat.com"},
                query={"token": "bad token\nwith control"},
            )
        )
    validate_handshake(
        FakeWebSocket(
            {"origin": "https://app.plexichat.com"}, query={"token": "abc.def_123"}
        )
    )


@pytest.mark.asyncio
async def test_gateway_rejects_forged_origin_before_accept(monkeypatch):
    monkeypatch.setattr(config, "get", _config())
    monkeypatch.setattr(
        "src.api.is_self_test_request", lambda request: False, raising=False
    )
    ws = FakeWebSocket({"origin": "https://evil.example"})
    await gateway_endpoint(ws)
    assert ws.accepted is False
    assert ws.closed is not None
    assert ws.closed[0] == 1008
