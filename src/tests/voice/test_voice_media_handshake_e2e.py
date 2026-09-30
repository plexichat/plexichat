"""Full voice media handshake through the real signaling manager.

The existing voice e2e tests stop at the signaling layer: they assert a
denial/allow decision, not that the media plane comes up. These drive the
complete path a client actually walks -- ``create_voice_connection`` ->
``handle_sdp_offer_async`` -> answer applied -> trickled ICE -- against the
production wiring (the ``src.core.voice`` MODULE handed to
``signaling.setup``, as ``server/initializer.py`` does) with a real aiortc
peer connection as the client, and assert the two things that matter:

* the peer connections reach ``connected`` and a media frame arrives at the
  SFU (not merely "an answer was produced"), and
* a trickled candidate is delivered to the SFU's peer connection, not just
  recorded on the signaling connection.
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

import src.core.voice as voice
import src.core.voice.signaling as signaling
from src.tests._conftest.waiting import wait_until_async

CONNECT_TIMEOUT_S = 15.0

# A syntactically valid, non-loopback host candidate. The manager's ICE
# validator rejects loopback, so this is the shape a real client would send.
HOST_CANDIDATE = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"


class _ToneTrack(MediaStreamTrack):
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


@pytest.fixture
def wired_call(
    db, auth_manager, server_manager, rel_manager, presence_manager, three_users
):
    """A real server + voice channel, wired the way production wires them."""
    owner, member, outsider = three_users
    server = server_manager.create_server(owner.id, "Media Handshake E2E")
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(
        owner.id,
        server.id,
        "voice-chat",
        channel_type=server_manager.ChannelType.VOICE,
    )

    voice.setup(
        db,
        auth_module=auth_manager,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
    )
    # The MODULE facade, exactly as server/initializer.py passes it.
    signaling.setup(
        voice_module=voice,
        events_module=None,
        sfu_backend="aiortc",
        stun_urls=["stun:stun.example.invalid:3478"],
        turn_urls=[],
        turn_secret="",
        turn_ttl=3600,
    )

    try:
        yield owner, member, server, channel
    finally:
        try:
            manager = signaling._get_manager()
        except Exception:
            manager = None
        if manager is not None:
            try:
                asyncio.get_event_loop().run_until_complete(manager.close())
            except Exception:
                pass
        signaling._manager = None
        signaling._setup_complete = False
        voice._manager = None
        voice._setup_complete = False


async def _start_call(owner_id: int, channel_id: int):
    """Drive the client half of the handshake; return (client, answer)."""
    voice.join_channel(owner_id, channel_id)
    signaling.create_voice_connection(owner_id, channel_id)

    client = RTCPeerConnection()
    client.addTrack(_ToneTrack())
    offer = await client.createOffer()
    await client.setLocalDescription(offer)

    answer = await signaling.handle_sdp_offer_async(
        owner_id, channel_id, offer.sdp, "offer"
    )
    await client.setRemoteDescription(
        RTCSessionDescription(sdp=answer.sdp, type="answer")
    )
    return client, answer


def _sfu_peer(channel_id: int, owner_id: int):
    manager = signaling._get_manager()
    sfu = manager._get_sfu()
    return sfu, sfu._rooms[f"voice_{channel_id}"][f"user_{owner_id}"]


def _remote_candidates(pc) -> list:
    """The SFU pc's ICE transport's view of the client's candidates."""
    for transceiver in pc.getTransceivers():
        transport = getattr(transceiver.receiver, "transport", None)
        ice_transport = getattr(transport, "transport", None)
        if ice_transport is not None:
            try:
                return ice_transport.getRemoteCandidates()
            except Exception:
                continue
    return []


@pytest.mark.asyncio
async def test_full_handshake_reaches_connected_and_media_flows(wired_call):
    owner, _member, _server, channel = wired_call
    cid = int(channel.id)

    client, answer = await _start_call(owner.id, cid)
    try:
        assert answer.sdp_type.value == "answer"
        assert "a=candidate:" in answer.sdp

        _sfu, peer = _sfu_peer(cid, owner.id)
        await wait_until_async(
            lambda: (
                peer.pc.connectionState == "connected"
                and client.connectionState == "connected"
            ),
            timeout=CONNECT_TIMEOUT_S,
            message=lambda: (
                f"never connected: sfu={peer.pc.connectionState} "
                f"client={client.connectionState}/{client.iceConnectionState}"
            ),
        )

        assert peer.incoming_audio_tracks, "SFU received no audio track"
        frame = await asyncio.wait_for(
            peer.incoming_audio_tracks[0].recv(), timeout=CONNECT_TIMEOUT_S
        )
        assert frame.samples > 0
    finally:
        await client.close()
        try:
            await signaling._get_manager().close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_trickled_candidate_reaches_the_sfu_media_plane(wired_call):
    """The recorded candidate must also be applied to the peer connection.

    Before the fix ``handle_ice_candidate(s)`` only appended the candidate to
    the signaling connection; the SFU pc's remote-candidate list stayed empty.
    On loopback ICE still completes anyway (peer-reflexive discovery), so only
    inspecting the media plane shows the difference.
    """
    owner, _member, _server, channel = wired_call
    cid = int(channel.id)

    client, _answer = await _start_call(owner.id, cid)
    try:
        _sfu, peer = _sfu_peer(cid, owner.id)
        await wait_until_async(
            lambda: peer.pc.remoteDescription is not None,
            timeout=CONNECT_TIMEOUT_S,
            message="SFU peer connection never applied the client's offer",
        )

        accepted = await signaling.handle_ice_candidate_async(
            owner.id, cid, HOST_CANDIDATE, "0", 0
        )
        assert accepted is True

        # aiortc records it asynchronously on the ICE transport.
        await wait_until_async(
            lambda: any(
                candidate.ip == "192.168.1.100" and candidate.port == 54321
                for candidate in _remote_candidates(peer.pc)
            ),
            timeout=5.0,
            message=lambda: (
                "the client's trickled candidate never reached the SFU peer "
                f"connection: {_remote_candidates(peer.pc)}"
            ),
        )

        # ...and it is still recorded on the signaling connection.
        connection = signaling._get_manager()._connections[owner.id]
        assert any(
            "192.168.1.100" in candidate.candidate
            for candidate in connection.ice_candidates
        )
    finally:
        await client.close()
        try:
            await signaling._get_manager().close()
        except Exception:
            pass
