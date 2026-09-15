"""The aiortc backend must actually apply a degradation hint, not just say no.

ANNEX AA recorded this residual: aiortc exposes no simulcast API, so the old
implementation simply returned ``False``. That was honest but useless --
``BandwidthEstimator`` reacts to a peer's congestion by asking the SFU to lower
what it sends that peer, and on the in-process backend nothing ever happened.

ANNEX AB then applied the hint by *suspending* the peer's outbound video
entirely and recorded the coarse-ness as a residual. These tests pin the
refinement (ANNEX AC): aiortc's ``RTCRtpSender`` has no ``setParameters`` and
no ``maxBitrate``, so the lever is a frame-rate bounded view of the track --
the degraded peer keeps video at a capped rate instead of losing it, the
frames it does not need are drained and dropped, and full rate is re-attached
when the hint is lifted. Audio is never touched.

The rate assertions are non-vacuous: against the suspend implementation the
sender holds ``None`` and delivers no frames at all.
"""

from __future__ import annotations

import asyncio

import pytest

try:
    from aiortc import MediaStreamTrack
except ImportError:
    pytest.skip("aiortc not installed", allow_module_level=True)

from src.core.voice.signaling.sfu import aiortc_adapter as adapter_mod
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AiortcAdapter,
    BoundedVideoTrack,
    DEGRADED_VIDEO_FPS,
)
from src.core.voice.signaling.sfu.base import MediaKind


class _StubVideoTrack(MediaStreamTrack):
    """A video track that is never pulled; only its identity/kind matter.

    ``kind`` must be a class attribute: the aiortc base declares it as a
    read-only property, so it cannot be set per instance.
    """

    kind = "video"

    async def recv(self):  # pragma: no cover - the SFU never pulls this
        await asyncio.sleep(3600)
        raise RuntimeError("stopped")


class _StubAudioTrack(MediaStreamTrack):
    """An audio track, to prove the hint never touches audio."""

    kind = "audio"

    async def recv(self):  # pragma: no cover - the SFU never pulls this
        await asyncio.sleep(3600)
        raise RuntimeError("stopped")


class _ScriptedVideoTrack(MediaStreamTrack):
    """A video source that yields ``total`` frames as fast as they are read."""

    kind = "video"

    def __init__(self, total: int) -> None:
        super().__init__()
        self.total = total
        self.read = 0
        self.ended = False

    async def recv(self):
        if self.read >= self.total:
            return None
        self.read += 1
        return object()  # opaque payload: the wrapper never inspects frames

    def stop(self) -> None:
        # Recorded so a test can prove the shared track was left running.
        self.ended = True


class _FakeClock:
    """A monotonic clock that advances a fixed step on every read."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.now
        self.now += self.step
        return current


async def _adapter_with_video_sender() -> tuple:
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_7", "user_9"
    await adapter.join_room(room_id, peer_id)
    peer = adapter._rooms[room_id][peer_id]
    video = _StubVideoTrack()
    sender = peer.pc.addTrack(video)
    return adapter, room_id, peer_id, peer, video, sender


@pytest.mark.asyncio
async def test_degradation_bounds_the_outbound_video_instead_of_stopping_it() -> None:
    """The hint must reduce what the sender emits, not blank the peer."""
    adapter, room, peer_id, peer, video, sender = await _adapter_with_video_sender()
    try:
        assert sender.track is video

        applied = await adapter.set_preferred_layers(room, peer_id, "", 0, 0)

        assert applied is True, "the degradation hint was silently ignored"
        bounded = sender.track
        assert bounded is not None, "video was stopped instead of bounded"
        assert isinstance(bounded, BoundedVideoTrack)
        assert bounded.source is video, "the original track was not wrapped"
        assert bounded.kind == "video"
        assert peer.degraded_senders[sender] is video
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_bounded_view_keeps_delivering_frames_at_a_capped_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded means *slower*, not stopped: frames still arrive, just fewer.

    The source produces 30 frames at 30fps; at the degraded ceiling only a
    fraction may be forwarded, and every source frame must still be read so
    the sender's queue cannot grow.
    """
    frames = 30
    source = _ScriptedVideoTrack(frames)
    clock = _FakeClock(step=1.0 / 30.0)
    monkeypatch.setattr(adapter_mod.time, "monotonic", clock)

    view = BoundedVideoTrack(source)

    forwarded = 0
    while True:
        frame = await view.recv()
        if frame is None:
            break
        forwarded += 1

    assert 1 < forwarded < frames, (
        f"expected a reduced but non-zero frame rate, got {forwarded}/{frames}"
    )
    assert forwarded <= int(frames * DEGRADED_VIDEO_FPS / 30.0) + 1
    assert source.read == frames, "dropped frames were not drained from the source"
    assert source.ended is False, "the shared source track was stopped"


@pytest.mark.asyncio
async def test_bounded_view_never_stops_the_shared_source() -> None:
    """Ending one degraded sender must not end video for the whole room."""
    source = _ScriptedVideoTrack(1)
    view = BoundedVideoTrack(source)

    view.stop()

    assert source.ended is False
    assert view.readyState == "ended"


@pytest.mark.asyncio
async def test_restore_reattaches_the_original_track() -> None:
    adapter, room, peer_id, peer, video, sender = await _adapter_with_video_sender()
    try:
        await adapter.set_preferred_layers(room, peer_id, "", 0, 0)

        restored = await adapter.set_preferred_layers(room, peer_id, "", 2, 2)

        assert restored is True
        assert sender.track is video, "video never came back to full rate"
        assert peer.degraded_senders == {}
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_repeated_hints_report_false_when_nothing_changes() -> None:
    """Idempotence: an already-applied hint is a no-op, not a fake success."""
    adapter, room, peer_id, _peer, _video, _sender = await _adapter_with_video_sender()
    try:
        assert await adapter.set_preferred_layers(room, peer_id, "", 0, 0) is True

        assert await adapter.set_preferred_layers(room, peer_id, "", 0, 0) is False
        assert await adapter.set_preferred_layers(room, peer_id, "", 2, 2) is True
        assert await adapter.set_preferred_layers(room, peer_id, "", 2, 2) is False
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_audio_only_peer_has_nothing_to_apply() -> None:
    """Audio must never be touched: only video is the bandwidth lever."""
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_8", "user_10"
    await adapter.join_room(room_id, peer_id)
    peer = adapter._rooms[room_id][peer_id]
    audio = _StubAudioTrack()
    sender = peer.pc.addTrack(audio)
    try:
        applied = await adapter.set_preferred_layers(room_id, peer_id, "", 0, 0)

        assert applied is False
        assert sender.track is audio, "audio must not be touched by a layer hint"
        assert peer.degraded_senders == {}
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_unknown_peer_reports_false() -> None:
    adapter = AiortcAdapter()
    try:
        assert (
            await adapter.set_preferred_layers("voice_1", "user_1", "", 0, 0) is False
        )
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_degraded_sender_is_not_duplicated_by_a_later_produce() -> None:
    """A degraded forwarded track counts as wired, so it is not re-added.

    The bounded view is not the original track object, so without the
    degraded-sender bookkeeping ``consume``/forwarding would see a sender that
    does not hold the track and add it a second time, producing a duplicate
    sender and a spurious renegotiation while the peer is degraded.
    """
    adapter = AiortcAdapter()
    room_id = "voice_9"
    await adapter.join_room(room_id, "user_1")
    await adapter.join_room(room_id, "user_2")
    peer1 = adapter._rooms[room_id]["user_1"]
    peer2 = adapter._rooms[room_id]["user_2"]

    video = _StubVideoTrack()
    sender = peer2.pc.addTrack(video)
    try:
        assert await adapter.set_preferred_layers(room_id, "user_2", "", 0, 0) is True
        assert isinstance(sender.track, BoundedVideoTrack)

        await adapter._forward_track_to_peer(
            video, "producer-1", MediaKind.VIDEO, peer2
        )

        assert len(peer2.pc.getSenders()) == 1, "duplicate sender while degraded"
        assert peer1.peer_id == "user_1"
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_closing_a_producer_releases_a_degraded_sender() -> None:
    """Ghost-sender cleanup must account for degraded senders too."""
    adapter = AiortcAdapter()
    room_id = "voice_10"
    await adapter.join_room(room_id, "user_1")
    await adapter.join_room(room_id, "user_2")
    peer2 = adapter._rooms[room_id]["user_2"]

    video = _StubVideoTrack()
    sender = peer2.pc.addTrack(video)
    try:
        await adapter.set_preferred_layers(room_id, "user_2", "", 0, 0)
        assert sender in peer2.degraded_senders

        adapter._remove_forwarded_senders(room_id, video)

        assert peer2.degraded_senders == {}
    finally:
        await adapter.close()
