"""Per-sender views on the aiortc SFU.

Two defects meet here. aiortc calls ``stop()`` on a sender's track when its
RTP task exits (``pc.close()`` / ``removeTrack``), so handing one shared track
to every sender let the first peer to leave end the room's media; and a shared
``RemoteStreamTrack`` is backed by a single queue, so several senders calling
``recv()`` on it *split* the frames between them instead of each receiving
them all. Each sender now gets its own fan-out view: it sees every frame, and
its ``stop()`` detaches that sender alone.
"""

import asyncio

import pytest

from src.core.voice.signaling.sfu import aiortc_adapter
from src.core.voice.signaling.sfu.acoustic_defense import (
    AcousticDefenseConfig,
    AudioProcessingTrack,
)
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AiortcAdapter,
    AIORTC_AVAILABLE,
)
from src.core.voice.signaling.sfu.base import MediaKind
from src.core.voice.signaling.sfu.fanout import FanoutView


# Referenced through the module so these tests fail on the behaviour they pin
# rather than on a missing name when run against the pre-fix code.
def _same_sender_track(a, b):
    return aiortc_adapter._same_sender_track(a, b)


pytestmark = pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc is not installed")

SDP = (
    "v=0\r\n"
    "o=- 1 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=ice-ufrag:abc\r\n"
    "a=ice-pwd:def\r\n"
)

ENABLED = AcousticDefenseConfig(enabled=True)


class _Frame:
    def __init__(self, data):
        self.data = data


def _speech():
    return _Frame([0.1] * 960)


class _SourceTrack:
    """Minimal aiortc-style source, consumable through ``recv``."""

    kind = "audio"
    label = "test-mic"
    readyState = "live"

    def __init__(self, frames):
        self._frames = list(frames)
        self.onended = None
        self.stopped = False

    async def recv(self):
        if not self._frames:
            return None
        return self._frames.pop(0)

    async def _read(self):
        return await self.recv()

    def stop(self):
        self.stopped = True


# ----------------------------------------------------------------------
# The wrapper contract
# ----------------------------------------------------------------------


class TestAudioProcessingTrackViews:
    def test_clone_is_a_distinct_view_sharing_a_key(self):
        source = _SourceTrack([_speech()])
        producer = AudioProcessingTrack(source, ENABLED)

        view = producer.clone()

        assert view is not producer
        assert view.id != producer.id
        assert view.share_key == producer.share_key

    @pytest.mark.asyncio
    async def test_a_view_stopping_does_not_end_the_shared_source(self):
        source = _SourceTrack([_speech(), _speech()])
        producer = AudioProcessingTrack(source, ENABLED)
        view = producer.clone()

        view.stop()

        # The shared source is still live and the producer still reads it.
        assert source.stopped is False
        assert await producer._read() is not None

    @pytest.mark.asyncio
    async def test_a_stopped_view_reports_eof_without_touching_the_source(self):
        source = _SourceTrack([_speech(), _speech()])
        producer = AudioProcessingTrack(source, ENABLED)
        view = producer.clone()
        view.stop()

        assert await view._read() is None
        assert source.stopped is False
        # One view stopping must not consume or end another view's stream.
        other = producer.clone()
        assert await other._read() is not None

    def test_the_owning_wrapper_still_stops_its_source(self):
        source = _SourceTrack([_speech()])
        producer = AudioProcessingTrack(source, ENABLED)

        producer.stop()

        assert source.stopped is True

    def test_views_of_one_wrapper_share_a_key_and_views_of_another_do_not(self):
        a = AudioProcessingTrack(_SourceTrack([]), ENABLED)
        b = AudioProcessingTrack(_SourceTrack([]), ENABLED)

        assert _same_sender_track(a, a.clone())
        assert _same_sender_track(a.clone(), a.clone())
        assert not _same_sender_track(a, b)
        assert not _same_sender_track(a.clone(), b.clone())

    @pytest.mark.asyncio
    async def test_sender_view_is_a_fanout_view_carrying_the_producers_key(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        wrapped = AudioProcessingTrack(_SourceTrack([]), ENABLED)

        view = adapter._sender_view(wrapped)

        assert isinstance(view, FanoutView)
        assert view is not wrapped
        assert view.fanout.source is wrapped
        assert view.share_key == wrapped.share_key


# ----------------------------------------------------------------------
# The adapter's forwarding path
# ----------------------------------------------------------------------


class _FakeSender:
    def __init__(self, track):
        self.track = track


class _Desc:
    def __init__(self, sdp):
        self.sdp = sdp


class _FakePC:
    def __init__(self, *args, **kwargs):
        self._handlers = {}
        self.localDescription = None
        self.remoteDescription = None
        self.signalingState = "stable"
        self.iceGatheringState = "complete"
        self.added_tracks = []
        self.senders = []
        self.receivers = []
        self.removed = []
        self.closed = False

    def on(self, event, handler=None):
        if handler is None:

            def deco(fn):
                self._handlers[event] = fn
                return fn

            return deco
        self._handlers[event] = handler
        return handler

    async def createOffer(self):
        return _Desc(SDP)

    async def setLocalDescription(self, desc):
        self.localDescription = desc

    def addTrack(self, track):
        self.added_tracks.append(track)

    def removeTrack(self, sender):
        self.removed.append(sender)
        if sender in self.senders:
            self.senders.remove(sender)

    def getSenders(self):
        return list(self.senders)

    def getReceivers(self):
        return self.receivers

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _stub_pc(monkeypatch):
    monkeypatch.setattr(aiortc_adapter, "RTCPeerConnection", _FakePC)


async def _produced_room(adapter, peers=3):
    """Join ``peers`` peers and produce an audio track from the first."""
    await adapter.join_room("room", "p1")
    for n in range(2, peers + 1):
        await adapter.join_room("room", f"p{n}")
    first = adapter._rooms["room"]["p1"]
    source = _SourceTrack([_speech() for _ in range(8)])
    first.incoming_audio_tracks.append(source)
    producer = await adapter.produce("room", "p1", "t", MediaKind.AUDIO, {})
    return first, source, producer


class TestForwardedAudioViews:
    @pytest.mark.asyncio
    async def test_each_sender_gets_its_own_view(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        first, _, producer = await _produced_room(adapter)

        p2 = adapter._rooms["room"]["p2"]
        p3 = adapter._rooms["room"]["p3"]
        assert len(p2.pc.added_tracks) == 1
        assert len(p3.pc.added_tracks) == 1
        view2, view3 = p2.pc.added_tracks[0], p3.pc.added_tracks[0]

        assert isinstance(view2, FanoutView)
        assert isinstance(view3, FanoutView)
        assert view2 is not view3
        # Neither sender holds the shared producer wrapper; both subscribe to
        # one relay over it, so both see the whole stream.
        assert view2 is not first.producer_tracks[producer.id]
        assert view2.fanout is view3.fanout
        assert view2.share_key == view3.share_key

    @pytest.mark.asyncio
    async def test_one_sender_leaving_does_not_stop_the_room_s_audio(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        first, source, producer = await _produced_room(adapter)
        p2 = adapter._rooms["room"]["p2"]
        p3 = adapter._rooms["room"]["p3"]
        view2, view3 = p2.pc.added_tracks[0], p3.pc.added_tracks[0]

        assert await asyncio.wait_for(view3.recv(), 2) is not None

        # Simulate aiortc's RTP task exiting for the first sender.
        view2.stop()

        assert source.stopped is False
        # The other sender's stream is untouched.
        assert await asyncio.wait_for(view3.recv(), 2) is not None
        assert first.producer_tracks[producer.id] is not None

    @pytest.mark.asyncio
    async def test_ghost_sender_sweep_finds_views_through_the_relay(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        first, _, producer = await _produced_room(adapter)
        p2 = adapter._rooms["room"]["p2"]
        view2 = p2.pc.added_tracks[0]
        sender = _FakeSender(view2)
        p2.pc.senders = [sender]

        adapter._remove_forwarded_senders("room", first.producer_tracks[producer.id])

        assert p2.pc.removed == [sender]

    @pytest.mark.asyncio
    async def test_joining_peer_receives_a_view_not_the_shared_track(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        await adapter.join_room("room", "p1")
        first = adapter._rooms["room"]["p1"]
        wrapper = AudioProcessingTrack(_SourceTrack([_speech(), _speech()]), ENABLED)
        first.producer_tracks["prod-1"] = wrapper

        await adapter.join_room("room", "p2")

        p2 = adapter._rooms["room"]["p2"]
        assert len(p2.pc.added_tracks) == 1
        added = p2.pc.added_tracks[0]
        assert added is not wrapper
        assert added.share_key == wrapper.share_key
