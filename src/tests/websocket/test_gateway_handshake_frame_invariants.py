"""Deterministic security invariants for the gateway boundary.

This suite pins three fail-closed properties without a database, network or
real crypto:

* a browser handshake carrying a cross-site ``Origin`` is rejected *before*
  the ASGI upgrade, and an empty / wildcard allowlist never becomes allow-all;
* a state-mutating opcode delivered to a still-unidentified connection is
  refused by the centralized identity gate with ``NOT_AUTHENTICATED``;
* oversized frames are rejected with the dedicated ``MESSAGE_TOO_BIG`` close
  code before any JSON/UTF-8 decoding, while bounded frames still decode.

These are deliberately narrow, fast invariants that complement the broader
behavioural websocket suites.
"""

from types import SimpleNamespace

import pytest

import utils.config as config
from src.api.websocket.boundaries import (
    MAX_FRAME_BYTES,
    MAX_PARSED_DEPTH,
    validate_ticket,
)
from src.api.websocket.frames import (
    FrameLimitExceeded,
    decode_inbound_frame,
    enforce_frame_size,
)
from src.api.websocket.gateway import gateway_endpoint
from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.handshake import (
    HANDSHAKE_REJECT_CLOSE_CODE,
    HandshakeRejected,
    origin_allowed,
    validate_handshake,
)
from src.api.websocket.opcodes import GatewayCloseCode, GatewayOpcode

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.websocket]


# --- helpers ---------------------------------------------------------------


class _FakeWebSocket:
    """Minimal ASGI socket double: only accept/close/headers/query are used."""

    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.accepted = False
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


def _config_get(websocket=None, api=None):
    api = api if api is not None else {"cors_origins": ["https://app.example.test"]}

    def get(key, default=None):
        if key == "websocket":
            return websocket or {}
        if key == "api":
            return api
        return default

    return get


# --- 1. cross-site handshake rejection ------------------------------------


def test_origin_allowlist_fails_closed_for_every_untrusted_shape(monkeypatch):
    """Any Origin that is not an exact allowlisted absolute origin is denied."""
    monkeypatch.setattr(config, "get", _config_get())
    for hostile in (
        "https://evil.example",
        "http://app.example.test",  # scheme mismatch
        "https://app.example.test.evil.example",  # suffix trick
        "not an origin",
        "//app.example.test",  # no scheme
    ):
        assert origin_allowed(hostile) is False, hostile


def test_empty_and_wildcard_allowlists_never_allow_a_browser(monkeypatch):
    monkeypatch.setattr(config, "get", _config_get(api={"cors_origins": []}))
    assert origin_allowed("https://app.example.test") is False

    monkeypatch.setattr(
        config, "get", _config_get(websocket={"allowed_origins": ["*"]})
    )
    assert origin_allowed("https://evil.example") is False


def test_absent_origin_is_a_non_browser_client_is_allowed(monkeypatch):
    monkeypatch.setattr(config, "get", _config_get())
    assert origin_allowed(None) is True
    assert origin_allowed("") is True


def test_validate_handshake_rejects_forged_origin_and_malformed_ticket(monkeypatch):
    monkeypatch.setattr(config, "get", _config_get())
    with pytest.raises(HandshakeRejected):
        validate_handshake(_FakeWebSocket({"origin": "https://evil.example"}))
    with pytest.raises(HandshakeRejected):
        validate_handshake(
            _FakeWebSocket(query={"token": "bad token\nwith control"})
        )
    # A well-formed ticket and an allowlisted origin pass the boundary gate.
    validate_handshake(
        _FakeWebSocket(
            {"Origin": "https://app.example.test"}, query={"token": "abc.def_123"}
        )
    )


def test_ticket_validator_rejects_separator_and_control_smuggling():
    """The handshake reuses the same ticket rule as IDENTIFY."""
    for bad in ("", "a b", "x\r\ny", "x;DROP", "a" * 5000, 12345):
        with pytest.raises(ValueError):
            validate_ticket(bad, "handshake_token")


@pytest.mark.asyncio
async def test_gateway_closes_cross_site_handshake_before_accept(monkeypatch):
    monkeypatch.setattr(config, "get", _config_get())
    monkeypatch.setattr(
        "src.api.is_self_test_request", lambda request: False, raising=False
    )
    ws = _FakeWebSocket({"origin": "https://evil.example"})
    await gateway_endpoint(ws)

    assert ws.accepted is False
    assert ws.closed is not None
    assert ws.closed[0] == HANDSHAKE_REJECT_CLOSE_CODE


# --- 2. unauthenticated mutating opcode ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "opcode",
    [
        GatewayOpcode.PRESENCE_UPDATE,
        GatewayOpcode.VOICE_STATE_UPDATE,
        GatewayOpcode.ARTIFACT_SUBSCRIBE,
        GatewayOpcode.TYPING_START,
    ],
)
async def test_mutating_opcode_requires_identified_connection(
    opcode, opcode_handler: OpcodeHandler, connection
):
    """The centralized identity gate denies every mutating opcode pre-handler."""
    assert connection.is_authenticated is False
    response_op, response_data, close_code = await opcode_handler.handle(
        connection, int(opcode), {}
    )
    assert (response_op, response_data) == (None, None)
    assert close_code == int(GatewayCloseCode.NOT_AUTHENTICATED)


def test_identity_gate_set_covers_every_mutating_opcode():
    """IDENTIFY / RESUME / HEARTBEAT are not mutating; the rest are gated."""
    from src.api.websocket.boundaries import MUTATING_OPCODES, requires_authentication

    for op in (GatewayOpcode.IDENTIFY, GatewayOpcode.RESUME, GatewayOpcode.HEARTBEAT):
        assert requires_authentication(int(op)) is False
        assert int(op) not in MUTATING_OPCODES
    for op in (
        GatewayOpcode.PRESENCE_UPDATE,
        GatewayOpcode.VOICE_CONNECT,
        GatewayOpcode.ARTIFACT_OP,
    ):
        assert requires_authentication(int(op)) is True


# --- 3. oversized frame rejection -----------------------------------------


def test_message_too_big_has_a_distinct_non_decode_close_code():
    assert int(GatewayCloseCode.MESSAGE_TOO_BIG) != int(GatewayCloseCode.DECODE_ERROR)


def test_oversized_frame_is_rejected_before_json_parsing():
    # Invalid UTF-8 AND far over the ceiling: the size guard must win.
    raw = b"\xff" * (MAX_FRAME_BYTES + 1)
    with pytest.raises(FrameLimitExceeded) as exc:
        decode_inbound_frame(raw)
    assert exc.value.close_code == int(GatewayCloseCode.MESSAGE_TOO_BIG)
    assert exc.value.limit <= MAX_FRAME_BYTES


def test_bounded_frames_still_decode_and_enforce_is_transparent():
    raw = b'{"op": 1, "d": {"x": 1}}'
    assert enforce_frame_size(raw) == raw
    assert decode_inbound_frame(raw) == {"op": 1, "d": {"x": 1}}


def test_configured_frame_limit_is_clamped_to_the_hard_cap(monkeypatch):
    import src.api.websocket.compression as compression
    from src.api.websocket.frames import inbound_frame_limit

    monkeypatch.setattr(compression, "_get_limits", lambda: (1024, 4096))
    assert inbound_frame_limit() == 1024
    with pytest.raises(FrameLimitExceeded):
        enforce_frame_size(b"x" * 1025)

    monkeypatch.setattr(
        compression, "_get_limits", lambda: (MAX_FRAME_BYTES * 4, MAX_FRAME_BYTES * 4)
    )
    assert inbound_frame_limit() == MAX_FRAME_BYTES


def test_decoded_shape_is_bounded_after_parsing():
    from src.core.security.hardening import HardeningViolation

    from src.api.websocket.boundaries import validate_parsed_structure

    deep = {"x": {"x": {"x": 1}}}
    # A small frame describing a pathologically deep object is refused.
    nested = deep
    for _ in range(MAX_PARSED_DEPTH + 3):
        nested = {"x": nested}
    with pytest.raises(HardeningViolation):
        validate_parsed_structure(nested)
