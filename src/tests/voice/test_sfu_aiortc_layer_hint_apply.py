"""The aiortc backend must actually apply a degradation hint, not just say no.

ANNEX AA recorded this residual: aiortc exposes no simulcast API, so the old
implementation simply returned ``False``. That was honest but useless --
``BandwidthEstimator`` reacts to a peer's congestion by asking the SFU to lower
what it sends that peer, and on the in-process backend nothing ever happened.

aiortc does expose ``RTCRtpSender.replaceTrack``, and that is the lever the
backend has: suspend the degraded peer's outbound video (the highest-bitrate
stream it receives) so its congestion eases, then re-attach the track when the
hint is lifted. These tests drive a real ``RTCPeerConnection`` and a real
sender, so they fail against the old always-``False`` implementation.
"""

from __future__ import annotations

import asyncio

import pytest

aiortc = pytest.importorskip("aiortc")

from aiortc import MediaStreamTrack  # noqa: E402

from src.core.voice.signaling.sfu.aiortc_adapter import AiortcAdapter  # noqa: E402
from src.core.voice.signaling.sfu.base import MediaKind  # noqa: E402


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


async def _adapter_with_video_sender() -> tuple:
    adapter = AiortcAdapter()
    room_id, peer_id = "voice_7", "user_9"
    await adapter.join_room(room_id, peer_id)
    peer = adapter._rooms[room_id][peer_id]
    video = _StubVideoTrack()
    sender = peer.pc.addTrack(video)
    return adapter, room_id, peer_id, peer, video, sender


@pytest.mark.asyncio
async def test_degradation_suspends_the_peers_outbound_video() -> None:
    adapter, room, peer_id, peer, video, sender = await _adapter_with_video_sender()
    try:
        assert sender.track is video

        applied = await adapter.set_preferred_layers(room, peer_id, "", 0, 0)

        assert applied is True, "the degradation hint was silently ignored"
        assert sender.track is None, "video was not suspended on degradation"
        assert peer.suspended_senders[sender] is video
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_restore_reattaches_the_original_track() -> None:
    adapter, room, peer_id, peer, video, sender = await _adapter_with_video_sender()
    try:
        await adapter.set_preferred_layers(room, peer_id, "", 0, 0)

        restored = await adapter.set_preferred_layers(room, peer_id, "", 2, 2)

        assert restored is True
        assert sender.track is video, "video never came back after recovery"
        assert peer.suspended_senders == {}
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
    """Audio must never be suspended: only video is the bandwidth lever."""
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
        assert peer.suspended_senders == {}
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
async def test_suspended_sender_is_not_duplicated_by_a_later_produce() -> None:
    """A suspended forwarded track counts as wired, so it is not re-added.

    Without the suspended-sender bookkeeping, ``consume``/forwarding would see
    ``sender.track is None`` and add the track a second time, producing a
    duplicate sender and a spurious renegotiation while the peer is degraded.
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
        assert sender.track is None

        await adapter._forward_track_to_peer(
            video, "producer-1", MediaKind.VIDEO, peer2
        )

        assert len(peer2.pc.getSenders()) == 1, "duplicate sender while degraded"
        assert peer1.peer_id == "user_1"
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_closing_a_producer_releases_a_suspended_sender() -> None:
    """Ghost-sender cleanup must account for suspended senders too."""
    adapter = AiortcAdapter()
    room_id = "voice_10"
    await adapter.join_room(room_id, "user_1")
    await adapter.join_room(room_id, "user_2")
    peer2 = adapter._rooms[room_id]["user_2"]

    video = _StubVideoTrack()
    sender = peer2.pc.addTrack(video)
    try:
        await adapter.set_preferred_layers(room_id, "user_2", "", 0, 0)
        assert sender in peer2.suspended_senders

        adapter._remove_forwarded_senders(room_id, video)

        assert peer2.suspended_senders == {}
    finally:
        await adapter.close()
