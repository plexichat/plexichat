"""Janus media path: in-band VideoRoom negotiation, trickle, and layers.

Janus terminates the WebRTC session itself, so the gateway must forward the
client's offer to the peer's publisher handle (``publish`` + ``jsep``) and hand
Janus's answer back. The client also talks to THIS gateway rather than to Janus
directly, so its trickled ICE candidates must be relayed. These cover the three
callbacks plus the fail-closed paths when Janus does not answer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.voice.signaling.exceptions import SFUConnectionError
from src.core.voice.signaling.sfu.janus import JanusAdapter

OFFER_SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
ANSWER_SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=setup:passive\r\n"


@pytest.fixture
def adapter() -> JanusAdapter:
    return JanusAdapter(api_url="http://localhost:8088/janus")


def _wire_peer(adapter: JanusAdapter, peer: str = "user_5") -> tuple[int, int]:
    session_id, handle_id = 111, 222
    adapter._janus_sessions["voice_3"] = session_id
    adapter._janus_handles["voice_3"] = {peer: handle_id}
    return session_id, handle_id


class TestApplyRemoteOffer:
    @pytest.mark.asyncio
    async def test_forwards_the_offer_and_returns_janus_answer(self, adapter):
        session_id, handle_id = _wire_peer(adapter)
        adapter._send_message = AsyncMock(
            return_value={"jsep": {"type": "answer", "sdp": ANSWER_SDP}}
        )

        result = await adapter.apply_remote_offer("voice_3", "user_5", OFFER_SDP)

        adapter._send_message.assert_awaited_once_with(
            session_id,
            handle_id,
            {"request": "publish", "audio": True, "video": True},
            jsep={"type": "offer", "sdp": OFFER_SDP},
        )
        assert result == {"sdp": ANSWER_SDP, "type": "answer"}

    @pytest.mark.asyncio
    async def test_no_jsep_from_janus_fails_closed(self, adapter):
        _wire_peer(adapter)
        adapter._send_message = AsyncMock(return_value={"plugindata": {"data": {}}})

        with pytest.raises(SFUConnectionError, match="did not return an SDP answer"):
            await adapter.apply_remote_offer("voice_3", "user_5", OFFER_SDP)

    @pytest.mark.asyncio
    async def test_unknown_peer_fails_closed(self, adapter):
        adapter._janus_sessions["voice_3"] = 111
        adapter._janus_handles["voice_3"] = {}

        with pytest.raises(SFUConnectionError, match="not in Janus room"):
            await adapter.apply_remote_offer("voice_3", "user_ghost", OFFER_SDP)


class TestAddIceCandidate:
    @pytest.mark.asyncio
    async def test_trickled_candidate_is_forwarded_to_janus(self, adapter):
        session_id, handle_id = _wire_peer(adapter)
        adapter._request = AsyncMock(return_value={"janus": "ack"})

        accepted = await adapter.add_ice_candidate(
            "voice_3", "user_5", "candidate:1 1 udp 1 10.0.0.1 5000 typ host", "0", 0
        )

        assert accepted is True
        adapter._request.assert_awaited_once_with(
            f"/{session_id}/{handle_id}",
            {
                "janus": "trickle",
                "candidate": {
                    "candidate": "candidate:1 1 udp 1 10.0.0.1 5000 typ host",
                    "sdpMid": "0",
                    "sdpMLineIndex": 0,
                },
            },
        )

    @pytest.mark.asyncio
    async def test_unknown_peer_is_not_forwarded(self, adapter):
        adapter._janus_sessions["voice_3"] = 111
        adapter._janus_handles["voice_3"] = {}
        adapter._request = AsyncMock()

        accepted = await adapter.add_ice_candidate(
            "voice_3", "user_ghost", "candidate:1 1 udp 1 10.0.0.1 5000 typ host"
        )

        assert accepted is False
        adapter._request.assert_not_awaited()


class TestSetPreferredLayers:
    @pytest.mark.asyncio
    async def test_empty_consumer_id_configures_every_peer_subscriber(self, adapter):
        adapter._janus_sessions["voice_3"] = 111
        adapter._janus_handles["voice_3"] = {
            "user_5_sub_prod_a": 222,
            "user_5_sub_prod_b": 333,
            "user_5": 444,
        }
        adapter._send_message = AsyncMock()

        applied = await adapter.set_preferred_layers("voice_3", "user_5", "", 0, 1)

        assert applied is True
        assert adapter._send_message.await_count == 2
        for call in adapter._send_message.await_args_list:
            assert call.args[0] == 111
            assert call.args[1] in (222, 333)
            assert call.args[2] == {
                "request": "configure",
                "substream": 0,
                "temporal": 1,
            }

    @pytest.mark.asyncio
    async def test_named_consumer_configures_only_that_handle(self, adapter):
        adapter._janus_sessions["voice_3"] = 111
        adapter._janus_handles["voice_3"] = {
            "user_5_sub_prod_a": 222,
            "user_5_sub_prod_b": 333,
        }
        adapter._send_message = AsyncMock()

        applied = await adapter.set_preferred_layers(
            "voice_3", "user_5", "user_5_sub_prod_b", 2, 2
        )

        assert applied is True
        adapter._send_message.assert_awaited_once_with(
            111, 333, {"request": "configure", "substream": 2, "temporal": 2}
        )

    @pytest.mark.asyncio
    async def test_no_subscriber_reports_failure(self, adapter):
        adapter._janus_sessions["voice_3"] = 111
        adapter._janus_handles["voice_3"] = {"user_5": 444}

        assert (
            await adapter.set_preferred_layers("voice_3", "user_5", "", 0, 0) is False
        )
