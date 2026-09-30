"""Real RTP to a *degraded* aiortc peer.

``test_sfu_aiortc_layer_hint_apply.py`` proves the hint rewrites the sender's
track to a ``BoundedVideoTrack`` -- but only by inspecting the sender, never by
sending a packet. That is the same gap the original "media path dead" bug hid
in: a valid description and a rewritten sender object both look correct while
no media moves at all.

This module closes it. It stands up the SFU as the offerer, connects a real
``RTCPeerConnection`` client that only receives, confirms video frames actually
arrive over RTP, then applies the degradation hint *mid-stream* and asserts the
peer keeps receiving video -- at a reduced rate, rather than a stopped sender
or a black picture. The source is a synthetic track encoded to real VP8.
"""

from __future__ import annotations

import asyncio
import fractions

import pytest

try:
    import av
    import numpy as np
    from aiortc import (
        MediaStreamTrack,
        RTCPeerConnection,
        RTCSessionDescription,
    )
except ImportError:
    pytest.skip("aiortc/av/numpy not installed", allow_module_level=True)

from src.core.voice.signaling.sfu.aiortc_adapter import (
    AiortcAdapter,
    BoundedVideoTrack,
)
from src.tests._conftest.waiting import wait_until_async

# ICE on loopback completes in well under a second; these are generous so a
# loaded box does not turn a working media path into a flake.
CONNECT_TIMEOUT_S = 15.0
FRAME_TIMEOUT_S = 12.0


class _VideoSource(MediaStreamTrack):
    """A synthetic video source encoding real frames at ~30fps."""

    kind = "video"

    def __init__(self) -> None:
        super().__init__()
        self.pts = 0
        self.frames_read = 0

    async def recv(self):
        await asyncio.sleep(1.0 / 30.0)
        arr = np.zeros((120, 160, 3), dtype=np.uint8)
        arr[:, :, 1] = (self.pts * 5) % 255
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, 30)
        self.pts += 1
        self.frames_read += 1
        return frame


class _Collector:
    """Drains a receiver track so arrival can be polled without losing frames."""

    def __init__(self) -> None:
        self.track = None
        self.frames: list = []
        self._task: asyncio.Task | None = None

    def attach(self, track) -> None:
        self.track = track
        self._task = asyncio.ensure_future(self._drain(track))

    async def _drain(self, track) -> None:
        try:
            while True:
                frame = await track.recv()
                self.frames.append(frame)
        except Exception:
            # Receiver track ended (pc closed); nothing more to collect.
            pass

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


async def _wait_for_frames(
    collector: _Collector,
    count: int,
    timeout: float = FRAME_TIMEOUT_S,
    message=None,
) -> bool:
    await wait_until_async(
        lambda: len(collector.frames) >= count,
        timeout=timeout,
        message=message
        or (lambda: f"only {len(collector.frames)} frames arrived; wanted {count}"),
    )
    return True


async def _connect_receiver(adapter, room_id: str, peer_id: str) -> tuple:
    """Connect a receive-only client to the SFU and complete the handshake.

    aiortc never emits ``negotiationneeded``, so the SFU is always the
    answerer: the client offers a recvonly video transceiver, the SFU already
    holds the video sender (added before the offer), and its answer binds the
    sender to that m-line so RTP can flow. Returns ``(client, collector)``
    once the connection is established.
    """
    client = RTCPeerConnection()
    collector = _Collector()

    @client.on("track")
    def on_track(track):
        collector.attach(track)

    client.addTransceiver("video", direction="recvonly")
    offer = await client.createOffer()
    await client.setLocalDescription(offer)

    answer = await adapter.apply_remote_offer(
        room_id, peer_id, client.localDescription.sdp
    )
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
    return client, collector


@pytest.mark.asyncio
async def test_a_degraded_peer_still_receives_video_over_rtp() -> None:
    """The degraded peer keeps receiving frames, not a frozen picture."""
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_media_1", "user_1"
    await adapter.join_room(room_id, peer_id)
    peer = adapter._rooms[room_id][peer_id]
    source = _VideoSource()
    sender = peer.pc.addTrack(source)

    client, collector = await _connect_receiver(adapter, room_id, peer_id)
    try:
        # Baseline: real RTP video reaches the client before any hint.
        await _wait_for_frames(
            collector,
            1,
            message=(
                "no video arrived before the degradation hint; the media path "
                "itself is broken, so this test cannot say anything about the hint"
            ),
        )
        assert collector.frames[0].width == 160

        applied = await adapter.set_preferred_layers(room_id, peer_id, "", 0, 0)
        assert applied is True, "the degradation hint was silently ignored"
        assert isinstance(sender.track, BoundedVideoTrack), (
            "the sender was not rewritten to a bounded view"
        )

        # The crux: more video arrives *after* the sender was degraded.
        baseline = len(collector.frames)
        await _wait_for_frames(
            collector,
            baseline + 1,
            message=(
                "the degraded peer stopped receiving video: "
                f"{baseline} frames total, none since the hint"
            ),
        )
    finally:
        await collector.stop()
        await client.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_a_peer_degraded_before_connecting_still_receives_video() -> None:
    """Degrading before the handshake must not produce a dead m-line.

    ``BandwidthEstimator`` can flag a peer before it has fully finished joining;
    bounding the sender first and negotiating after is the other ordering, and
    it must still deliver media.
    """
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_media_2", "user_2"
    await adapter.join_room(room_id, peer_id)
    peer = adapter._rooms[room_id][peer_id]
    source = _VideoSource()
    sender = peer.pc.addTrack(source)

    applied = await adapter.set_preferred_layers(room_id, peer_id, "", 0, 0)
    assert applied is True
    assert isinstance(sender.track, BoundedVideoTrack)

    client, collector = await _connect_receiver(adapter, room_id, peer_id)
    try:
        await _wait_for_frames(
            collector,
            1,
            message="a peer degraded before connecting received no video at all",
        )
    finally:
        await collector.stop()
        await client.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_restoring_the_hint_keeps_video_flowing() -> None:
    """Lifting the hint re-attaches full rate without tearing video down."""
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_media_3", "user_3"
    await adapter.join_room(room_id, peer_id)
    peer = adapter._rooms[room_id][peer_id]
    source = _VideoSource()
    sender = peer.pc.addTrack(source)

    client, collector = await _connect_receiver(adapter, room_id, peer_id)
    try:
        await _wait_for_frames(collector, 1)

        assert await adapter.set_preferred_layers(room_id, peer_id, "", 0, 0)
        baseline = len(collector.frames)
        await _wait_for_frames(collector, baseline + 1)

        restored = await adapter.set_preferred_layers(room_id, peer_id, "", 2, 2)
        assert restored is True
        assert sender.track is source, "full rate was not re-attached"

        baseline = len(collector.frames)
        await _wait_for_frames(
            collector,
            baseline + 1,
            message="video stopped after the hint was lifted",
        )
    finally:
        await collector.stop()
        await client.close()
        await adapter.close()
