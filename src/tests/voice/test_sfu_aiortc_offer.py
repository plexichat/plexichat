"""aiortc inbound offer: the adapter must answer it, and keep the track.

``apply_remote_offer`` set the client's offer as the remote description and
then read ``peer.pc.localDescription`` -- which is ``None`` until an answer is
built and installed. The caller therefore fell back to ``create_transport()``,
whose ``createOffer()``/``setLocalDescription()`` raises ``InvalidStateError``
in the ``have-remote-offer`` state, so the client received synthetic SDP and no
media could ever flow on the default in-process backend. The answer is now
built and installed here, on every offer so renegotiation works too.

These tests drive a real ``RTCPeerConnection`` as the client, because the
offer/answer exchange is the whole point -- a stubbed pc would not exercise the
signaling states that made the original path fail.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

aiortc = pytest.importorskip("aiortc")

from aiortc import RTCPeerConnection, RTCSessionDescription  # noqa: E402

from src.core.voice.signaling.sfu import aiortc_adapter as mod  # noqa: E402
from src.core.voice.signaling.sfu.aiortc_adapter import AiortcAdapter  # noqa: E402
from src.core.voice.signaling.sfu.acoustic_defense import (  # noqa: E402
    AcousticDefenseConfig,
)
from src.core.voice.signaling.sfu.base import TransportDirection  # noqa: E402
from src.core.voice.signaling.exceptions import SFUConnectionError  # noqa: E402


async def _client_offer() -> tuple[RTCPeerConnection, RTCSessionDescription]:
    client = RTCPeerConnection()
    client.createDataChannel("client-data")
    offer = await client.createOffer()
    await client.setLocalDescription(offer)
    return client, client.localDescription


@pytest.mark.asyncio
async def test_apply_remote_offer_returns_a_real_answer():
    """Regression: the answer must be built, not just the offer applied."""
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_11", "user_22"
    await adapter.join_room(room_id, peer_id)

    client, offer = await _client_offer()
    try:
        result = await adapter.apply_remote_offer(room_id, peer_id, offer.sdp)

        assert result["type"] == "answer"
        # A usable answer carries the SFU's ICE and DTLS parameters.
        assert "a=ice-ufrag" in result["sdp"]
        assert "a=ice-pwd" in result["sdp"]
        assert "a=fingerprint" in result["sdp"]

        # The client accepts it, and the SFU peer is back in "stable" -- the
        # state media (and later renegotiation) requires.
        await client.setRemoteDescription(
            RTCSessionDescription(sdp=result["sdp"], type="answer")
        )
        assert client.signalingState == "stable"
        assert adapter._rooms[room_id][peer_id].pc.signalingState == "stable"
    finally:
        await client.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_apply_remote_offer_answers_again_on_renegotiation():
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_33", "user_44"
    await adapter.join_room(room_id, peer_id)

    client, offer = await _client_offer()
    try:
        first = await adapter.apply_remote_offer(room_id, peer_id, offer.sdp)
        await client.setRemoteDescription(
            RTCSessionDescription(sdp=first["sdp"], type="answer")
        )

        # A second offer renegotiates (a new media section); the adapter must
        # answer it, not hand back the previous -- now stale -- local
        # description and leave the peer stuck in "have-remote-offer".
        client.addTransceiver("audio", direction="recvonly")
        reoffer = await client.createOffer()
        await client.setLocalDescription(reoffer)
        second = await adapter.apply_remote_offer(room_id, peer_id, reoffer.sdp)

        assert second["type"] == "answer"
        assert second["sdp"] != first["sdp"]
        assert adapter._rooms[room_id][peer_id].pc.signalingState == "stable"
    finally:
        await client.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_create_transport_reports_a_pending_remote_offer_clearly():
    """The state the old fallback crashed in, now a diagnosable error."""
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_55", "user_66"
    await adapter.join_room(room_id, peer_id)

    client, offer = await _client_offer()
    try:
        peer = adapter._rooms[room_id][peer_id]
        await peer.pc.setRemoteDescription(
            RTCSessionDescription(sdp=offer.sdp, type="offer")
        )
        assert peer.pc.localDescription is None

        with pytest.raises(SFUConnectionError, match="have-remote-offer"):
            await adapter.create_transport(room_id, peer_id, TransportDirection.SEND)
    finally:
        await client.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_on_track_keeps_the_raw_track_when_processing_fails(monkeypatch):
    """A failed acoustic-defense wrap must not lose the peer's track."""

    def boom(*args, **kwargs):
        raise RuntimeError("dsp unavailable")

    monkeypatch.setattr(mod, "AudioProcessingTrack", boom)

    adapter = AiortcAdapter(acoustic_defense=AcousticDefenseConfig(enabled=True))
    room_id, peer_id = "voice_77", "user_88"
    await adapter.join_room(room_id, peer_id)
    try:
        peer = adapter._rooms[room_id][peer_id]
        handler = peer.pc.listeners("track")[0]
        track = SimpleNamespace(kind="audio")

        await handler(track)

        # The track is still registered, so it can be produced and forwarded.
        assert peer.incoming_audio_tracks == [track]
    finally:
        await adapter.close()
