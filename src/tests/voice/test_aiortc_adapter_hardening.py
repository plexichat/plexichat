"""Hardening and compatibility tests for the aiortc SFU adapter package."""

from __future__ import annotations

import pytest

from src.core.voice.signaling.sfu import aiortc_adapter as pkg
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AIORTC_AVAILABLE,
    AiortcAdapter,
    BoundedVideoTrack,
    _same_sender_track,
    validate_ice_candidate_payload,
    validate_identifier,
    validate_payload_mapping,
    validate_sdp_payload,
)

pytestmark = pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc is not installed")


class _DummyPC:
    def __init__(self, *_a, **_k):
        self.localDescription = None
        self.remoteDescription = None
        self.iceGatheringState = "complete"
        self.signalingState = "stable"
        self.closed = False

    def on(self, event, handler=None):
        if handler is None:

            def deco(fn):
                return fn

            return deco
        return handler

    def createDataChannel(self, _label):
        return object()

    async def createOffer(self):
        class _D:
            sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

        return _D()

    async def createAnswer(self):
        class _D:
            sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            type = "answer"

        return _D()

    async def setLocalDescription(self, d):
        self.localDescription = d

    async def setRemoteDescription(self, _d):
        self.remoteDescription = object()
        self.signalingState = "have-remote-offer"

    async def close(self):
        self.closed = True

    def getReceivers(self):
        return []

    def getSenders(self):
        return []


def _adapter(monkeypatch: pytest.MonkeyPatch) -> AiortcAdapter:
    monkeypatch.setattr(pkg, "RTCPeerConnection", _DummyPC)
    return AiortcAdapter()


class TestCompatSurface:
    def test_package_exports(self) -> None:
        assert pkg.AiortcAdapter is AiortcAdapter
        assert callable(validate_identifier)
        assert hasattr(pkg, "_parse_ice_params_from_sdp")
        assert hasattr(pkg, "_parse_dtls_params_from_sdp")
        assert hasattr(pkg, "_parse_ice_candidates_from_sdp")
        assert hasattr(pkg, "AIORTC_AVAILABLE")

    def test_same_sender_track_identity(self) -> None:
        obj = object()
        assert _same_sender_track(obj, obj)
        assert not _same_sender_track(object(), object())


class TestValidation:
    @pytest.mark.parametrize(
        "bad",
        ["", " ", "../x", "peer id", "peer\x00id", "a" * 129, "peer\nid"],
    )
    def test_rejects_bad_identifier(self, bad: str) -> None:
        with pytest.raises(Exception):
            validate_identifier(bad, "peer_id")

    @pytest.mark.parametrize("good", ["peer-1", "room_2", "abc.DEF:1", "12345"])
    def test_accepts_good_identifier(self, good: str) -> None:
        assert validate_identifier(good) == good

    def test_rejects_malformed_sdp(self) -> None:
        with pytest.raises(Exception):
            validate_sdp_payload("not-sdp")
        with pytest.raises(Exception):
            validate_sdp_payload("v=0\r\n")
        with pytest.raises(Exception):
            validate_sdp_payload("x" * (300 * 1024))

    def test_accepts_sdp(self) -> None:
        sdp = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\nm=audio 9 RTP/AVP 0\r\n"
        assert validate_sdp_payload(sdp) == sdp

    def test_rejects_malformed_candidate(self) -> None:
        with pytest.raises(Exception):
            validate_ice_candidate_payload("bad", sdp_mid="0")
        with pytest.raises(Exception):
            validate_ice_candidate_payload("candidate:1 1 udp 1 1.2.3.4 9 typ host")
        with pytest.raises(Exception):
            validate_ice_candidate_payload(
                "candidate:x\nbad 1 udp 1 1.2.3.4 9 typ host", sdp_mid="0"
            )

    def test_accepts_candidate(self) -> None:
        cand, mid, idx = validate_ice_candidate_payload(
            "candidate:1 1 udp 2122260223 192.168.1.2 5000 typ host",
            sdp_mid="0",
            sdp_mline_index=0,
        )
        assert cand.startswith("candidate:")
        assert mid == "0"
        assert idx == 0

    def test_rejects_deep_or_huge_payload(self) -> None:
        nested: dict[str, object] = {"a": "b"}
        cur = nested
        for i in range(20):
            nxt: dict[str, object] = {f"k{i}": "v"}
            cur["n"] = nxt
            cur = nxt
        with pytest.raises(Exception):
            validate_payload_mapping(nested)
        with pytest.raises(Exception):
            validate_payload_mapping({f"k{i}": i for i in range(1000)})
        with pytest.raises(Exception):
            validate_payload_mapping({"x": "a" * (80 * 1024)})


@pytest.mark.asyncio
class TestAdapterHardening:
    async def test_rejects_malformed_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter(monkeypatch)
        with pytest.raises(Exception):
            await adapter.create_room("../evil")
        with pytest.raises(Exception):
            await adapter.join_room("room-1", "bad id")

    async def test_rejects_malformed_sdp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.core.voice.signaling.exceptions import SFUConnectionError

        adapter = _adapter(monkeypatch)
        await adapter.join_room("room-s1", "peer-1")
        with pytest.raises(SFUConnectionError):
            await adapter.apply_remote_offer(
                "room-s1", "peer-1", "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n"
            )

    async def test_rejects_malformed_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _adapter(monkeypatch)
        await adapter.join_room("room-c1", "peer-1")
        assert (
            await adapter.add_ice_candidate("room-c1", "peer-1", "garbage", sdp_mid="0")
            is False
        )
        assert (
            await adapter.add_ice_candidate(
                "room-c1", "peer-1", "candidate:1 1 udp 1 1.2.3.4 9 typ host"
            )
            is False
        )

    async def test_bounded_video_track_does_not_stop_source(self) -> None:
        class _Src:
            kind = "video"
            label = "s"
            stopped = False

            async def recv(self):
                return object()

            def stop(self):
                self.stopped = True

        src = _Src()
        bt = BoundedVideoTrack(src)
        bt.stop()
        assert not src.stopped
