"""The SDP answer must be bound to the caller's channel and session.

The client has always sent ``channel_id`` on ``VOICE_SDP_ANSWER`` and the handler
never read it (found by sweep H). An answer was applied to whatever signaling
connection the caller happened to own, with no check that it named the same
channel -- and an answer with no connection at all returned success while
nothing was applied (the M1 silent-success shape).

These tests pin the three refusals and the one acceptance.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.handlers.voice import VoiceHandler
from src.api.websocket.opcodes import GatewayCloseCode


def _connection(user_id: int = 7):
    return SimpleNamespace(is_authenticated=True, user_id=user_id)


def _wire(monkeypatch, *, connection=None, sfu=None, manager=True):
    import src.core.voice.signaling as signaling

    if not manager:
        monkeypatch.setattr(signaling, "_manager", None, raising=False)
        return None
    mgr = MagicMock()
    mgr._connections = {} if connection is None else {7: connection}
    mgr._get_sfu.return_value = (
        sfu if sfu is not None else MagicMock(apply_remote_answer=AsyncMock())
    )
    monkeypatch.setattr(signaling, "_manager", mgr)
    return mgr


@pytest.mark.asyncio
async def test_applies_the_answer_for_the_bound_channel(monkeypatch) -> None:
    sfu = MagicMock(apply_remote_answer=AsyncMock())
    conn = SimpleNamespace(channel_id=55, sfu_room_id="voice_55", sfu_peer_id="user_7")
    _wire(monkeypatch, connection=conn, sfu=sfu)

    result = await VoiceHandler().handle_voice_sdp_answer(
        _connection(), {"channel_id": 55, "sdp": "v=0"}
    )

    assert result == (None, None, None)
    sfu.apply_remote_answer.assert_awaited_once_with("voice_55", "user_7", "v=0")


@pytest.mark.asyncio
async def test_refuses_an_answer_that_names_another_channel(monkeypatch) -> None:
    """The binding is the persisted connection's channel, not the payload's."""
    sfu = MagicMock(apply_remote_answer=AsyncMock())
    conn = SimpleNamespace(channel_id=55, sfu_room_id="voice_55", sfu_peer_id="user_7")
    _wire(monkeypatch, connection=conn, sfu=sfu)

    result = await VoiceHandler().handle_voice_sdp_answer(
        _connection(), {"channel_id": 999, "sdp": "v=0"}
    )

    assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))
    sfu.apply_remote_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_requires_a_channel_id(monkeypatch) -> None:
    """The field is mandatory now that the server reads it."""
    _wire(monkeypatch)

    for payload in (
        {"sdp": "v=0"},
        {"channel_id": "", "sdp": "v=0"},
        {"channel_id": "nope", "sdp": "v=0"},
        {"channel_id": 0, "sdp": "v=0"},
    ):
        result = await VoiceHandler().handle_voice_sdp_answer(_connection(), payload)
        assert result == (None, None, int(GatewayCloseCode.DECODE_ERROR)), payload


@pytest.mark.asyncio
async def test_refuses_an_answer_with_no_session(monkeypatch) -> None:
    """M1: an answer nothing can be applied to must not look applied."""
    sfu = MagicMock(apply_remote_answer=AsyncMock())
    _wire(monkeypatch, connection=None, sfu=sfu)

    result = await VoiceHandler().handle_voice_sdp_answer(
        _connection(), {"channel_id": 55, "sdp": "v=0"}
    )

    assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))
    sfu.apply_remote_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_deployment_without_signaling_is_not_an_error(monkeypatch) -> None:
    """Unwired signaling has no media plane; the answer is simply not applied."""
    _wire(monkeypatch, manager=False)

    result = await VoiceHandler().handle_voice_sdp_answer(
        _connection(), {"channel_id": 55, "sdp": "v=0"}
    )

    assert result == (None, None, None)
