"""The gateway half of SFU-initiated renegotiation.

The signaling manager decides that a peer needs an offer and calls the
registered offer sender; this is that sender. It must push the offer as a
``VOICE_SDP_OFFER`` frame to the user's gateway connections, and it must do so
only while the user is genuinely still in the call -- the offer carries the
SFU's media parameters, so sending it to someone who has left would leak
signaling for a session they are no longer part of.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode


def _handler(dispatcher) -> OpcodeHandler:
    handler = OpcodeHandler.__new__(OpcodeHandler)
    handler._dispatcher = dispatcher
    return handler


@pytest.fixture
def voice_in_channel(monkeypatch):
    """Make the authoritative voice state report the user is in a channel."""
    import src.core.voice as voice

    state = SimpleNamespace(channel_id=5)
    monkeypatch.setattr(voice, "get_voice_state", lambda user_id: state, raising=False)
    return state


@pytest.mark.asyncio
async def test_offer_is_pushed_as_the_sdp_offer_opcode(voice_in_channel) -> None:
    dispatcher = SimpleNamespace(
        dispatch_raw_to_users=AsyncMock(return_value=1),
    )
    handler = _handler(dispatcher)

    sent = await handler._send_voice_renegotiation_offer(7, 5, "<offer>")

    assert sent is True
    dispatcher.dispatch_raw_to_users.assert_awaited_once()
    args = dispatcher.dispatch_raw_to_users.await_args
    assert args.args[0] == [7]
    assert args.args[1] == GatewayOpcode.VOICE_SDP_OFFER
    payload = args.args[2]
    assert payload["sdp"] == "<offer>"
    assert payload["type"] == "offer"
    assert payload["channel_id"] == "5"
    assert payload["renegotiation"] is True


@pytest.mark.asyncio
async def test_offer_is_dropped_when_the_user_left_the_call(monkeypatch) -> None:
    import src.core.voice as voice

    monkeypatch.setattr(voice, "get_voice_state", lambda user_id: None, raising=False)
    dispatcher = SimpleNamespace(dispatch_raw_to_users=AsyncMock(return_value=1))
    handler = _handler(dispatcher)

    sent = await handler._send_voice_renegotiation_offer(7, 5, "<offer>")

    assert sent is False
    dispatcher.dispatch_raw_to_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_offer_is_dropped_when_the_user_moved_channel(monkeypatch) -> None:
    import src.core.voice as voice

    monkeypatch.setattr(
        voice,
        "get_voice_state",
        lambda user_id: SimpleNamespace(channel_id=9),
        raising=False,
    )
    dispatcher = SimpleNamespace(dispatch_raw_to_users=AsyncMock(return_value=1))
    handler = _handler(dispatcher)

    assert await handler._send_voice_renegotiation_offer(7, 5, "<offer>") is False
    dispatcher.dispatch_raw_to_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_delivery_when_the_voice_state_cannot_be_read(monkeypatch) -> None:
    """An unverifiable roster must fail closed, not push the offer blind."""
    import src.core.voice as voice

    def boom(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(voice, "get_voice_state", boom, raising=False)
    dispatcher = SimpleNamespace(dispatch_raw_to_users=AsyncMock(return_value=1))
    handler = _handler(dispatcher)

    assert await handler._send_voice_renegotiation_offer(7, 5, "<offer>") is False
    dispatcher.dispatch_raw_to_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_delivery_when_every_connection_was_gone(voice_in_channel) -> None:
    """sent == 0 means the user had no live socket; that is not a success."""
    dispatcher = SimpleNamespace(dispatch_raw_to_users=AsyncMock(return_value=0))
    handler = _handler(dispatcher)

    assert await handler._send_voice_renegotiation_offer(7, 5, "<offer>") is False
