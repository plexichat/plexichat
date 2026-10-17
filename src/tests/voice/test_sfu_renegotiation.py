"""SFU-initiated renegotiation.

aiortc never emits ``negotiationneeded``, so the SFU can never start an offer:
a producer forwarded to a peer that has already completed its handshake lands
on a transceiver the client never learns about, and the track silently never
arrives. The adapter now builds that offer and hands it to a renegotiation
sink, which the signaling manager implements: it maps the SFU peer back to a
user, refuses anyone without a live connection bound to the channel, and calls
the registered offer deliverer.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.voice.signaling.manager import SignalingManager
from src.core.voice.signaling.models import SignalingState, VoiceConnection
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AiortcAdapter,
    AIORTC_AVAILABLE,
)

pytestmark = pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc is not installed")


# ----------------------------------------------------------------------
# The adapter's sink
# ----------------------------------------------------------------------


class _Desc:
    def __init__(self, sdp, kind="offer"):
        self.sdp = sdp
        self.type = kind


class _FakePC:
    def __init__(self, *args, **kwargs):
        self._handlers = {}
        self.localDescription = None
        self.remoteDescription = None
        self.signalingState = "stable"
        self.iceGatheringState = "complete"
        self.senders = []

    def on(self, event, handler=None):
        if handler is None:

            def deco(fn):
                self._handlers[event] = fn
                return fn

            return deco
        self._handlers[event] = handler
        return handler

    async def createOffer(self):
        return _Desc("v=0\r\nm=video 9 ...\r\n")

    async def setLocalDescription(self, desc):
        self.localDescription = desc

    def addTrack(self, track):
        sender = MagicMock()
        sender.track = track
        self.senders.append(sender)
        return sender

    def getSenders(self):
        return list(self.senders)

    def getReceivers(self):
        return []

    async def close(self):
        pass


class _RawVideoTrack:
    kind = "video"
    label = "cam"

    def __init__(self):
        self.stopped = False

    async def recv(self):
        return None

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _stub_pc(monkeypatch):
    import src.core.voice.signaling.sfu.aiortc_adapter as mod

    monkeypatch.setattr(mod, "RTCPeerConnection", _FakePC)


@pytest.mark.asyncio
async def test_offer_is_built_and_handed_to_the_sink() -> None:
    adapter = AiortcAdapter()
    delivered = []

    def sink(room_id, peer_id, sdp):
        delivered.append((room_id, peer_id, sdp))

    adapter.set_renegotiation_sink(sink)
    await adapter.join_room("voice_1", "user_2")
    peer = adapter._rooms["voice_1"]["user_2"]
    peer.pc.remoteDescription = _Desc("remote", "offer")

    ok = await adapter._request_renegotiation("voice_1", "user_2", peer)

    assert ok is True
    assert len(delivered) == 1
    assert delivered[0][0] == "voice_1"
    assert delivered[0][1] == "user_2"
    assert delivered[0][2].startswith("v=0")
    assert peer.pc.localDescription.sdp == delivered[0][2]


@pytest.mark.asyncio
async def test_offer_is_dropped_honestly_without_a_sink() -> None:
    """No signaling path must report False, not a silent success."""
    adapter = AiortcAdapter()
    await adapter.join_room("voice_1", "user_2")
    peer = adapter._rooms["voice_1"]["user_2"]
    peer.pc.remoteDescription = _Desc("remote", "offer")

    assert await adapter._request_renegotiation("voice_1", "user_2", peer) is False


@pytest.mark.asyncio
async def test_no_offer_before_the_peer_has_negotiated() -> None:
    """A peer that has not offered yet binds its tracks on that first offer."""
    adapter = AiortcAdapter()
    delivered = []
    adapter.set_renegotiation_sink(lambda *a: delivered.append(a))
    await adapter.join_room("voice_1", "user_2")
    peer = adapter._rooms["voice_1"]["user_2"]
    # remoteDescription is still None.
    assert await adapter._request_renegotiation("voice_1", "user_2", peer) is False
    assert delivered == []


@pytest.mark.asyncio
async def test_async_sink_is_awaited() -> None:
    adapter = AiortcAdapter()
    seen = []

    async def sink(room_id, peer_id, sdp):
        seen.append(sdp)

    adapter.set_renegotiation_sink(sink)
    await adapter.join_room("voice_1", "user_2")
    peer = adapter._rooms["voice_1"]["user_2"]
    peer.pc.remoteDescription = _Desc("remote", "offer")

    assert await adapter._request_renegotiation("voice_1", "user_2", peer) is True
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_forwarding_to_a_connected_peer_delivers_an_offer() -> None:
    """The forwarding path is where the offer is actually needed."""
    adapter = AiortcAdapter()
    delivered = []
    adapter.set_renegotiation_sink(
        lambda room, peer, sdp: delivered.append((room, peer))
    )
    await adapter.join_room("voice_1", "user_2")
    target = adapter._rooms["voice_1"]["user_2"]
    target.pc.remoteDescription = _Desc("remote", "offer")

    await adapter._forward_track_to_peer(
        _RawVideoTrack(), "producer-1", MagicMock(), target
    )

    assert delivered == [("voice_1", "user_2")]


# ----------------------------------------------------------------------
# The manager's delivery
# ----------------------------------------------------------------------


def _manager() -> SignalingManager:
    return SignalingManager(sfu_backend="aiortc")


def _bind(manager, user_id: int, channel_id: int) -> None:
    manager._connections[user_id] = VoiceConnection(
        user_id=user_id,
        channel_id=channel_id,
        session_id="sess",
        state=SignalingState.CONNECTED,
        created_at=0,
        last_activity=0,
    )


@pytest.fixture
def offer_sender(monkeypatch):
    from src.core.voice import signaling

    calls = []

    async def sender(user_id, channel_id, sdp):
        calls.append((user_id, channel_id, sdp))
        return True

    monkeypatch.setattr(signaling, "_offer_sender", sender, raising=False)
    return calls


@pytest.mark.asyncio
async def test_manager_delivers_to_a_bound_connection(offer_sender) -> None:
    manager = _manager()
    _bind(manager, 7, 5)

    ok = await manager.on_renegotiation_needed("voice_5", "user_7", "<offer>")

    assert ok is True
    assert offer_sender == [(7, 5, "<offer>")]


@pytest.mark.asyncio
async def test_manager_refuses_a_user_with_no_connection(offer_sender) -> None:
    manager = _manager()

    ok = await manager.on_renegotiation_needed("voice_5", "user_7", "<offer>")

    assert ok is False
    assert offer_sender == [], "media signaling leaked to a user not in a call"


@pytest.mark.asyncio
async def test_manager_refuses_a_connection_bound_to_another_channel(
    offer_sender,
) -> None:
    manager = _manager()
    _bind(manager, 7, 99)

    ok = await manager.on_renegotiation_needed("voice_5", "user_7", "<offer>")

    assert ok is False
    assert offer_sender == []


@pytest.mark.asyncio
async def test_manager_ignores_unmappable_ids(offer_sender) -> None:
    manager = _manager()
    _bind(manager, 7, 5)

    assert await manager.on_renegotiation_needed("bogus", "user_7", "<o>") is False
    assert await manager.on_renegotiation_needed("voice_5", "bogus", "<o>") is False
    assert offer_sender == []


@pytest.mark.asyncio
async def test_manager_reports_false_without_a_registered_sender(
    monkeypatch,
) -> None:
    from src.core.voice import signaling

    monkeypatch.setattr(signaling, "_offer_sender", None, raising=False)
    manager = _manager()
    _bind(manager, 7, 5)

    assert await manager.on_renegotiation_needed("voice_5", "user_7", "<o>") is False
