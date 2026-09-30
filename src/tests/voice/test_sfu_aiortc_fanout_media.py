"""Real RTP to *several* subscribers of one produced track.

The fan-out unit tests prove a shared source is read once and replicated.
This drives the same path over real RTP: one producer track forwarded by the
adapter to two receive-only clients, which must both see video. It is what
catches a relay that is wired correctly in isolation but starves one peer in
the media path -- the failure mode the relay exists to remove.
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

from src.core.voice.signaling.sfu.aiortc_adapter import AiortcAdapter
from src.core.voice.signaling.sfu.base import MediaKind
from src.tests._conftest.waiting import wait_until_async

CONNECT_TIMEOUT_S = 15.0
FRAME_TIMEOUT_S = 12.0


class _VideoSource(MediaStreamTrack):
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
    def __init__(self) -> None:
        self.frames: list = []
        self._task: asyncio.Task | None = None

    def attach(self, track) -> None:
        self._task = asyncio.ensure_future(self._drain(track))

    async def _drain(self, track) -> None:
        try:
            while True:
                frame = await track.recv()
                self.frames.append(frame)
        except Exception:
            pass

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


async def _connect_receiver(adapter, room_id: str, peer_id: str) -> tuple:
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
            f"never connected: sfu={peer.pc.connectionState} "
            f"client={client.connectionState}/{client.iceConnectionState}"
        ),
    )
    return client, collector


@pytest.mark.asyncio
async def test_two_subscribers_both_receive_the_produced_track() -> None:
    adapter = AiortcAdapter()
    room_id = "voice_fanout"
    await adapter.join_room(room_id, "pub")
    await adapter.join_room(room_id, "sub1")
    await adapter.join_room(room_id, "sub2")

    source = _VideoSource()
    # The publisher's track arrives through the peer's track handler; producing
    # it then forwards one shared track to every other peer.
    adapter._rooms[room_id]["pub"].incoming_video_tracks.append(source)
    await adapter.produce(room_id, "pub", "t", MediaKind.VIDEO, {})

    c1, col1 = await _connect_receiver(adapter, room_id, "sub1")
    c2, col2 = await _connect_receiver(adapter, room_id, "sub2")
    try:
        # Both are served by ONE read of the source; neither may be starved.
        await wait_until_async(
            lambda: len(col1.frames) >= 3,
            timeout=FRAME_TIMEOUT_S,
            message=lambda: f"sub1 received only {len(col1.frames)} frames",
        )
        await wait_until_async(
            lambda: len(col2.frames) >= 3,
            timeout=FRAME_TIMEOUT_S,
            message=lambda: f"sub2 received only {len(col2.frames)} frames",
        )
    finally:
        await col1.stop()
        await col2.stop()
        await c1.close()
        await c2.close()
        await adapter.close()
