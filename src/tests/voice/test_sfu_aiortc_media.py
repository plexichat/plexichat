"""Real RTP media end to end on the in-process aiortc backend.

The offer/answer tests (``test_sfu_aiortc_offer.py``) prove a usable *answer*
is produced and accepted. They deliberately stop there: a valid description is
necessary but not sufficient for media, and the earlier "SFU/media path dead"
bug (``apply_remote_offer`` returning ``{}``) produced a *synthetic* answer that
also parsed. Those tests could not tell the two apart.

This module closes that gap. It drives a real ``RTCPeerConnection`` as the
client, sends an actual audio track, and waits for the peer connections to
reach ``connected`` -- then asserts a frame is received on the SFU side. That is
the difference between "signaling succeeded" and "media flowed".
"""

from __future__ import annotations

import asyncio

import pytest

try:
    from aiortc import (
        RTCPeerConnection,
        RTCSessionDescription,
        MediaStreamTrack,
    )
except ImportError:
    pytest.skip("aiortc not installed", allow_module_level=True)

from src.core.voice.signaling.sfu.aiortc_adapter import AiortcAdapter
from src.tests._conftest.waiting import wait_until_async

# ICE on loopback completes in well under a second; these are generous so a
# loaded CI box does not turn a working media path into a flake.
CONNECT_TIMEOUT_S = 15.0


class _ToneTrack(MediaStreamTrack):
    """A synthetic audio track: 20ms of silence at 48kHz, forever."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self.pts = 0

    async def recv(self):
        import av

        await asyncio.sleep(0.02)
        frame = av.AudioFrame(format="s16", layout="mono", samples=960)
        frame.sample_rate = 48000
        frame.pts = self.pts
        self.pts += 960
        return frame


async def _offer_from(client: RTCPeerConnection) -> RTCSessionDescription:
    offer = await client.createOffer()
    await client.setLocalDescription(offer)
    return client.localDescription


@pytest.mark.asyncio
async def test_media_flows_from_client_to_the_aiortc_sfu() -> None:
    """The whole point of the media plane: a frame arrives at the SFU."""
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_11", "user_22"
    await adapter.join_room(room_id, peer_id)

    client = RTCPeerConnection()
    client.addTrack(_ToneTrack())
    try:
        offer = await _offer_from(client)
        answer = await adapter.apply_remote_offer(room_id, peer_id, offer.sdp)
        assert answer.get("sdp"), "SFU produced no answer"

        await client.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type="answer")
        )

        peer = adapter._rooms[room_id][peer_id]
        await wait_until_async(
            lambda: (
                peer.pc.connectionState == "connected"
                and client.connectionState == "connected"
            ),
            timeout=CONNECT_TIMEOUT_S,
            message=lambda: (
                f"never connected: sfu={peer.pc.connectionState}/"
                f"{peer.pc.iceConnectionState} client={client.connectionState}/"
                f"{client.iceConnectionState}"
            ),
        )

        assert peer.incoming_audio_tracks, "SFU received no audio track"
        frame = await asyncio.wait_for(
            peer.incoming_audio_tracks[0].recv(), timeout=CONNECT_TIMEOUT_S
        )
        assert frame.samples > 0
        assert frame.sample_rate == 48000
    finally:
        await client.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_answer_carries_the_sfus_ice_candidate() -> None:
    """The answer must be connectable without a second round trip.

    ``apply_remote_offer`` does not wait on ``iceGatheringState``; the answer it
    returns is only usable if the SFU's host candidate is already in the SDP.
    If that ever regresses the candidate must be trickled instead, and nothing
    in the client flow does -- so assert it is present.
    """
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_33", "user_44"
    await adapter.join_room(room_id, peer_id)

    client = RTCPeerConnection()
    client.addTrack(_ToneTrack())
    try:
        offer = await _offer_from(client)
        answer = await adapter.apply_remote_offer(room_id, peer_id, offer.sdp)

        assert answer["type"] == "answer"
        assert "a=ice-ufrag" in answer["sdp"]
        assert "a=ice-pwd" in answer["sdp"]
        assert "a=fingerprint" in answer["sdp"]
        assert "a=candidate:" in answer["sdp"], (
            "answer has no ICE candidate and nothing trickles the SFU's "
            "candidates to the client, so ICE could never complete"
        )
    finally:
        await client.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_produce_uses_the_media_track_the_client_sent() -> None:
    """Media that reached the SFU can be produced/forwarded, not just stored."""
    from src.core.voice.signaling.sfu.base import MediaKind

    adapter = AiortcAdapter()
    room_id, peer_id = "voice_55", "user_66"
    await adapter.join_room(room_id, peer_id)

    client = RTCPeerConnection()
    client.addTrack(_ToneTrack())
    try:
        offer = await _offer_from(client)
        answer = await adapter.apply_remote_offer(room_id, peer_id, offer.sdp)
        await client.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type="answer")
        )
        peer = adapter._rooms[room_id][peer_id]
        await wait_until_async(
            lambda: bool(peer.incoming_audio_tracks),
            timeout=CONNECT_TIMEOUT_S,
            message="SFU received no audio track from the client",
        )

        producer = await adapter.produce(
            room_id, peer_id, "transport-1", MediaKind.AUDIO, {"codecs": []}
        )

        assert producer.kind == MediaKind.AUDIO
        assert peer.producer_tracks.get(producer.id) is not None
    finally:
        await client.close()
        await adapter.close()
