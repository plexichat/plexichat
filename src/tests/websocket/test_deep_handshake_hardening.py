"""Deep hardening regressions for the gateway handshake boundary.

Covers four real weaknesses in the pre-upgrade gate:

1. Origin gate failing open when headers are not a readable mapping.
2. Token gate failing open when query params are not a readable mapping.
3. Self-test exemption being used to smuggle a malformed handshake ticket.
4. Unbounded anonymous handshake floods (connection/task exhaustion).
"""

from types import SimpleNamespace

import pytest

import utils.config as config
from src.api.websocket import handshake as hs
from src.api.websocket.handshake import (
    HANDSHAKE_THROTTLE_CLOSE_CODE,
    HandshakeRejected,
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
        self.headers = {} if headers is None else headers
        self.query_params = {} if query is None else query
        self.client = SimpleNamespace(host=host)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    hs.reset_handshake_state()
    monkeypatch.setattr(config, "get", _config())
    yield
    hs.reset_handshake_state()


def test_unreadable_headers_fail_closed(monkeypatch):
    """An object that cannot expose headers must not skip the origin gate."""
    # An opaque object is present but has no .get and is not a pair sequence.
    ws = FakeWebSocket(headers=object())
    with pytest.raises(HandshakeRejected) as exc:
        validate_handshake(ws)
    assert "could not be inspected" in exc.value.reason


def test_titlecase_origin_dict_is_enforced(monkeypatch):
    """A dict keyed 'Origin' (title case) must not slip past the gate."""
    with pytest.raises(HandshakeRejected):
        validate_handshake(FakeWebSocket(headers={"Origin": "https://evil.example"}))
    validate_handshake(FakeWebSocket(headers={"Origin": "https://app.plexichat.com"}))


def test_readable_header_pair_sequence_still_enforced(monkeypatch):
    """Headers supplied as (name, value) pairs are parsed, not waved through."""
    bad = FakeWebSocket(headers=[(b"origin", b"https://evil.example")])
    with pytest.raises(HandshakeRejected):
        validate_handshake(bad)

    good = FakeWebSocket(headers=[(b"Origin", "https://app.plexichat.com")])
    validate_handshake(good)


def test_unreadable_query_fails_closed(monkeypatch):
    """An unparseable query object must not silently drop the token check."""
    ws = FakeWebSocket(query=object())
    with pytest.raises(HandshakeRejected) as exc:
        validate_handshake(ws)
    assert "query could not be inspected" in exc.value.reason


def test_selftest_cannot_smuggle_malformed_ticket(monkeypatch):
    """The self-test exemption relaxes origin only, not ticket shape."""
    # No token: remains allowed (legacy behavior preserved).
    validate_handshake(FakeWebSocket(), is_selftest=True)
    # A malformed ticket is rejected even on a self-test connection.
    with pytest.raises(HandshakeRejected) as exc:
        validate_handshake(
            FakeWebSocket(query={"token": "bad token\nwith control"}),
            is_selftest=True,
        )
    assert "token" in exc.value.reason.lower()


def test_selftest_exempt_from_flood_limit(monkeypatch):
    monkeypatch.setattr(
        config, "get", _config(websocket={"handshake_rate_limit_per_minute": 2})
    )
    for _ in range(10):
        validate_handshake(FakeWebSocket(), is_selftest=True)


def test_handshake_flood_is_bounded(monkeypatch):
    """Anonymous Origin-less handshakes are capped per peer before upgrade."""
    monkeypatch.setattr(
        config, "get", _config(websocket={"handshake_rate_limit_per_minute": 3})
    )
    ws = FakeWebSocket()
    for _ in range(3):
        validate_handshake(ws)
    with pytest.raises(HandshakeRejected) as exc:
        validate_handshake(ws)
    assert exc.value.close_code == HANDSHAKE_THROTTLE_CLOSE_CODE


def test_flood_limit_is_per_peer(monkeypatch):
    monkeypatch.setattr(
        config, "get", _config(websocket={"handshake_rate_limit_per_minute": 1})
    )
    validate_handshake(FakeWebSocket(host="198.51.100.1"))
    # A different peer gets its own budget.
    validate_handshake(FakeWebSocket(host="198.51.100.2"))
    with pytest.raises(HandshakeRejected):
        validate_handshake(FakeWebSocket(host="198.51.100.1"))


def test_flood_limit_counts_rejected_attempts(monkeypatch):
    monkeypatch.setattr(
        config, "get", _config(websocket={"handshake_rate_limit_per_minute": 2})
    )
    forged = FakeWebSocket(headers={"origin": "https://evil.example"})
    with pytest.raises(HandshakeRejected):
        validate_handshake(forged)
    with pytest.raises(HandshakeRejected):
        validate_handshake(forged)
    # Even an otherwise-valid handshake from the same peer is now throttled.
    with pytest.raises(HandshakeRejected) as exc:
        validate_handshake(FakeWebSocket())
    assert exc.value.close_code == HANDSHAKE_THROTTLE_CLOSE_CODE
