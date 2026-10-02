"""VOICE_SDP_ANSWER reply must serialize snowflake ids as strings.

``handle_voice_sdp_offer`` answers inline via gateway.py ``send_json``,
bypassing ``dispatcher._build_dispatch_payload`` and its
``stringify_snowflake_ids`` backstop. A raw int ``from_user_id`` crosses the
wire as a JSON number and loses precision past 2^53 in JS clients (the same
class of bug fixed for DISPATCH payloads, ratchet routes, and VOICE_SPEAKING).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.websocket.handlers.voice import VoiceHandler
from src.api.websocket.opcodes import GatewayOpcode


def _connection(user_id: int):
    return SimpleNamespace(is_authenticated=True, user_id=user_id)


@pytest.mark.asyncio
async def test_sdp_offer_answer_serializes_from_user_id_as_string(monkeypatch) -> None:
    """The reply frame carries from_user_id as a decimal string."""
    import src.core.voice.signaling as signaling

    async def _answer(user_id, channel_id, sdp):
        return SimpleNamespace(sdp="v=0", sdp_type=SimpleNamespace(value="answer"))

    monkeypatch.setattr(signaling, "handle_sdp_offer_async", _answer)

    # Use a snowflake past 2^53 where a JSON number would lose precision.
    user_id = 9007199254740993
    op, data, close = await VoiceHandler().handle_voice_sdp_offer(
        _connection(user_id), {"channel_id": "123", "sdp": "v=0"}
    )

    assert close is None
    assert op == int(GatewayOpcode.VOICE_SDP_ANSWER)
    assert data is not None
    assert data["from_user_id"] == str(user_id)
    assert isinstance(data["from_user_id"], str)
    assert data["sdp"] == "v=0"
