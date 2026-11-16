"""Opcode and intent allowlist boundaries."""

import pytest

from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.intents import ALL_INTENTS, validate_intents
from src.api.websocket.opcodes import (
    CLIENT_OPCODES,
    KNOWN_OPCODES,
    SERVER_ONLY_OPCODES,
    GatewayCloseCode,
    GatewayOpcode,
    coerce_opcode,
)
from src.api.websocket.session import SessionManager
from src.core.events.types import GatewayIntent
from src.core.security.hardening import HardeningViolation


def test_known_opcodes_partition():
    assert CLIENT_OPCODES.isdisjoint(SERVER_ONLY_OPCODES)
    assert CLIENT_OPCODES | SERVER_ONLY_OPCODES == KNOWN_OPCODES


def test_server_push_opcodes_are_not_client_sendable():
    for op in (
        GatewayOpcode.DISPATCH,
        GatewayOpcode.HELLO,
        GatewayOpcode.HEARTBEAT_ACK,
        GatewayOpcode.RECONNECT,
        GatewayOpcode.INVALID_SESSION,
        GatewayOpcode.ARTIFACT_SYNC,
        GatewayOpcode.SUM_SYNC,
    ):
        assert int(op) not in CLIENT_OPCODES


def test_coerce_opcode_accepts_known():
    assert coerce_opcode(int(GatewayOpcode.IDENTIFY)) == int(GatewayOpcode.IDENTIFY)


@pytest.mark.parametrize("value", [True, False, "2", 1.0, None, 999, -1])
def test_coerce_opcode_rejects_invalid(value):
    with pytest.raises(HardeningViolation):
        coerce_opcode(value)


def test_intents_allowlist():
    assert validate_intents(0) is True
    assert validate_intents(ALL_INTENTS) is True
    assert (
        validate_intents(GatewayIntent.GUILDS | GatewayIntent.DIRECT_MESSAGES) is True
    )
    assert validate_intents(-1) is False
    assert validate_intents(ALL_INTENTS + 1) is False
    assert validate_intents(ALL_INTENTS | (1 << 30)) is False
    assert validate_intents(True) is False
    assert validate_intents("513") is False


@pytest.mark.asyncio
async def test_handler_rejects_bool_opcode():
    from unittest.mock import MagicMock

    from src.api.websocket.connection import Connection

    handler = OpcodeHandler(session_manager=SessionManager())
    conn = Connection(websocket=MagicMock(), connection_id="bool-op")
    _, _, close_code = await handler.handle(conn, True, {})
    assert close_code == int(GatewayCloseCode.UNKNOWN_OPCODE)


@pytest.mark.asyncio
async def test_handler_rejects_unknown_opcode():
    from unittest.mock import MagicMock

    from src.api.websocket.connection import Connection

    handler = OpcodeHandler(session_manager=SessionManager())
    conn = Connection(websocket=MagicMock(), connection_id="unknown-op")
    _, _, close_code = await handler.handle(conn, 12345, {})
    assert close_code == int(GatewayCloseCode.UNKNOWN_OPCODE)
