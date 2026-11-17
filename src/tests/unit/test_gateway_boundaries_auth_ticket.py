"""Mutating-opcode auth gate and reconnect/ticket field boundaries."""

from unittest.mock import MagicMock

import pytest

from src.api.websocket.boundaries import (
    MAX_BACKOFF_MS,
    MAX_RESUME_SEQUENCE,
    MUTATING_OPCODES,
    bounded_backoff,
    bounded_resume_sequence,
    requires_authentication,
    validate_properties,
    validate_session_id,
    validate_ticket,
)
from src.api.websocket.connection import Connection
from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.opcodes import GatewayCloseCode, GatewayOpcode
from src.api.websocket.session import SessionManager
from src.core.security.hardening import HardeningViolation


def test_mutating_opcode_classification():
    for op in (
        GatewayOpcode.PRESENCE_UPDATE,
        GatewayOpcode.TYPING_START,
        GatewayOpcode.VOICE_CONNECT,
        GatewayOpcode.ARTIFACT_OP,
        GatewayOpcode.SUM_OP,
    ):
        assert int(op) in MUTATING_OPCODES
        assert requires_authentication(int(op)) is True
    for op in (GatewayOpcode.IDENTIFY, GatewayOpcode.RESUME, GatewayOpcode.HEARTBEAT):
        assert requires_authentication(int(op)) is False


@pytest.mark.asyncio
async def test_unauthenticated_mutating_opcode_is_rejected():
    handler = OpcodeHandler(session_manager=SessionManager())
    conn = Connection(websocket=MagicMock(), connection_id="anon")
    _, _, close_code = await handler.handle(conn, GatewayOpcode.VOICE_CONNECT, {})
    assert close_code == int(GatewayCloseCode.NOT_AUTHENTICATED)


@pytest.mark.parametrize("value", ["", "bad token", "bad\x00token", "x" * 5000, 12345])
def test_invalid_tickets_rejected(value):
    with pytest.raises(HardeningViolation):
        validate_ticket(value)


def test_valid_ticket_accepted():
    assert validate_ticket("test_token") == "test_token"
    assert validate_ticket("a.b-c_d123") == "a.b-c_d123"


@pytest.mark.parametrize("value", ["not-hex", "session with spaces", 123])
def test_invalid_session_ids_rejected(value):
    with pytest.raises(HardeningViolation):
        validate_session_id(value)


def test_valid_session_id_accepted():
    assert validate_session_id("a" * 32) == "a" * 32


@pytest.mark.parametrize("value", [-1, MAX_RESUME_SEQUENCE + 1, 2**63 - 1, True])
def test_resume_sequence_bounded(value):
    with pytest.raises(HardeningViolation):
        bounded_resume_sequence(value)


def test_backoff_bounded():
    assert bounded_backoff(1000) == 1000
    with pytest.raises(HardeningViolation):
        bounded_backoff(MAX_BACKOFF_MS + 1)
    with pytest.raises(HardeningViolation):
        bounded_backoff(-1)


def test_properties_bounded_without_modifying_valid_values():
    props = {"os": "linux", "browser": "pytest"}
    assert validate_properties(props) == props
    with pytest.raises(HardeningViolation):
        validate_properties({"browser": "x" * 500})
