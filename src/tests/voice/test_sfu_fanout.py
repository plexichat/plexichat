"""The fan-out relay and the per-sender views it feeds.

A ``RemoteStreamTrack`` is backed by a single queue, so two senders calling
``recv()`` on the same track each *take* a frame -- the stream is split, not
shared. The SFU forwards one produced track to every other peer, so without a
relay each peer receives a fraction of the media, and every sender object still
looks correct. ``TrackFanout`` owns the single read loop and copies each frame
into a bounded per-view queue; each sender gets its own :class:`FanoutView`,
which sees the whole stream and whose ``stop()`` detaches that sender alone.

This one suite covers both halves: the relay's delivery contract, and the
adapter wiring that puts a view on each sender.
"""

import asyncio

import pytest

from src.core.voice.signaling.sfu import aiortc_adapter
from src.core.voice.signaling.sfu.acoustic_defense import (
    AcousticDefenseConfig,
    AudioProcessingTrack,
)
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AIORTC_AVAILABLE,
    AiortcAdapter,
)
from src.core.voice.signaling.sfu.base import MediaKind
from src.core.voice.signaling.sfu.fanout import DEFAULT_MAX_LAG, FanoutView, TrackFanout

# ----------------------------------------------------------------------
# The relay
# ----------------------------------------------------------------------


class _ScriptedSource:
    """Yields ``total`` integer frames, then ``None`` (end of stream)."""

    kind = "audio"
    label = "scripted"

    def __init__(self, total: int):
        self.total = total
        self.read = 0

    async def recv(self):
        # Yield between frames so a consumer can drain as production proceeds,
        # the way real-time media arrives rather than as one burst.
        await asyncio.sleep(0)
        if self.read >= self.total:
            return None
        self.read += 1
        return self.read

    def stop(self):
        self.stopped = True


class _BurstSource:
    """Yields frames as fast as it is read, with no yield to the event loop."""

    kind = "audio"
    label = "burst"

    def __init__(self, total: int):
        self.total = total
        self.read = 0

    async def recv(self):
        if self.read >= self.total:
            return None
        self.read += 1
        return self.read

    def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_every_subscriber_receives_every_frame() -> None:
    source = _ScriptedSource(5)
    relay = TrackFanout(source)

    a = relay.subscribe()
    b = relay.subscribe()

    got_a = [await a.recv() for _ in range(5)]
    got_b = [await b.recv() for _ in range(5)]

    assert got_a == [1, 2, 3, 4, 5], "first view missed frames"
    assert got_b == [1, 2, 3, 4, 5], "second view got a *different* stream"
    assert source.read == 5, "the source was read more than once per frame"

    await relay.aclose()


@pytest.mark.asyncio
async def test_end_of_stream_reaches_every_view() -> None:
    relay = TrackFanout(_ScriptedSource(1))
    a = relay.subscribe()
    b = relay.subscribe()

    assert await a.recv() == 1
    assert await b.recv() == 1
    assert await asyncio.wait_for(a.recv(), 2) is None
    assert await asyncio.wait_for(b.recv(), 2) is None

    await relay.aclose()


@pytest.mark.asyncio
async def test_a_stopped_view_does_not_affect_the_others() -> None:
    relay = TrackFanout(_ScriptedSource(4))
    a = relay.subscribe()
    b = relay.subscribe()

    a.stop()

    assert relay.subscriber_count == 1
    assert await asyncio.wait_for(b.recv(), 2) == 1
    # A stopped view reports end of stream rather than reading the relay.
    assert a.readyState == "ended"
    assert await a.recv() is None
    assert getattr(a.fanout.source, "stopped", False) is False

    await relay.aclose()


@pytest.mark.asyncio
async def test_a_lagging_view_sheds_frames_without_blocking_the_rest() -> None:
    """One slow sender must not stall the read loop for everyone else."""
    relay = TrackFanout(_ScriptedSource(40), max_lag=2)
    slow = relay.subscribe()
    fast = relay.subscribe()

    # Drain the fast view continuously; never touch the slow one.
    received = []
    for _ in range(40):
        frame = await asyncio.wait_for(fast.recv(), 2)
        if frame is None:
            break
        received.append(frame)

    assert received == list(range(1, 41)), "the fast view lost frames"
    assert relay.dropped_frames > 0, "the slow view did not shed under back-pressure"
    # Shedding, not growing: the slow view's queue stays within its budget.
    assert slow._queue.qsize() <= 2

    await relay.aclose()


@pytest.mark.asyncio
async def test_pacing_delivers_every_frame_from_a_bursting_source() -> None:
    """Read the source at the consumer's pace, not the source's.

    A source that bursts must not outrun the consumer: reading as fast as it
    produces fills the bounded queue and sheds every frame the consumer has
    not taken. Pacing the read loop to the fastest view means an able consumer
    sees the whole stream -- against the unpaced loop this test sees only the
    last ``max_lag`` frames.
    """
    total = 40
    relay = TrackFanout(_BurstSource(total), max_lag=4)
    view = relay.subscribe()

    got = []
    while True:
        frame = await asyncio.wait_for(view.recv(), 2)
        if frame is None:
            break
        got.append(frame)

    assert got == list(range(1, total + 1)), "the burst outran the consumer"
    assert relay.dropped_frames == 0

    await relay.aclose()


@pytest.mark.asyncio
async def test_subscribing_after_a_view_stops_restarts_the_relay() -> None:
    relay = TrackFanout(_ScriptedSource(6))
    first = relay.subscribe()
    assert await first.recv() == 1
    first.stop()
    # Let the read loop observe that it has no subscribers.
    await asyncio.sleep(0)

    second = relay.subscribe()
    assert second._queue.maxsize == DEFAULT_MAX_LAG
    # Whatever remains is delivered to the new view.
    assert await asyncio.wait_for(second.recv(), 2) is not None

    await relay.aclose()


# ----------------------------------------------------------------------
# The adapter's forwarding path
# ----------------------------------------------------------------------


ENABLED = AcousticDefenseConfig(enabled=True)

SDP = (
    "v=0\r\n"
    "o=- 1 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=ice-ufrag:abc\r\n"
    "a=ice-pwd:def\r\n"
)


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


@pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc is not installed")
class TestForwardedTrackViews:
    @pytest.mark.asyncio
    async def test_sender_view_is_a_fanout_view_over_the_produced_track(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        wrapped = AudioProcessingTrack(_SourceTrack([]), ENABLED)

        view = adapter._sender_view(wrapped)

        assert isinstance(view, FanoutView)
        assert view is not wrapped
        assert view.fanout.source is wrapped
        # Sender hygiene can match the view back to the track it came from.
        assert aiortc_adapter._same_sender_track(view, wrapped)

    @pytest.mark.asyncio
    async def test_each_sender_gets_its_own_view(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        first, _, producer = await self._produced_room(adapter)

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

    @pytest.mark.asyncio
    async def test_one_sender_leaving_does_not_stop_the_room_s_audio(self):
        adapter = AiortcAdapter(acoustic_defense=ENABLED)
        first, source, producer = await self._produced_room(adapter)
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
        first, _, producer = await self._produced_room(adapter)
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
        assert aiortc_adapter._same_sender_track(added, wrapper)

    async def _produced_room(self, adapter, peers=3):
        """Join ``peers`` peers and produce an audio track from the first."""
        await adapter.join_room("room", "p1")
        for n in range(2, peers + 1):
            await adapter.join_room("room", f"p{n}")
        first = adapter._rooms["room"]["p1"]
        source = _SourceTrack([_speech() for _ in range(8)])
        first.incoming_audio_tracks.append(source)
        producer = await adapter.produce("room", "p1", "t", MediaKind.AUDIO, {})
        return first, source, producer
