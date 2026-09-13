"""Tests for the aiortc SFU adapter.

Covers the pure SDP parsing helpers, room lifecycle, transport
creation/connection, produce/consume wiring, pause/resume/close
semantics, capabilities, and recording. The real ``aiortc`` package is
installed, but all tests stub ``RTCPeerConnection`` so no network or
event-loop stack is exercised.
"""

import os

import pytest
from unittest.mock import Mock

from src.core.voice.signaling.exceptions import SFUConnectionError
from src.core.voice.signaling.sfu import aiortc_adapter
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AiortcAdapter,
    AIORTC_AVAILABLE,
    _parse_dtls_params_from_sdp,
    _parse_ice_candidates_from_sdp,
    _parse_ice_params_from_sdp,
)
from src.core.voice.signaling.sfu.base import (
    AcousticDefenseConfig,
    MediaKind,
    RoomInfo,
    SFUTransport,
    TransportDirection,
)
from src.core.voice.signaling.sfu.acoustic_defense import AudioProcessingTrack

# The adapter raises at construction when aiortc is unavailable; skip the
# whole module in such environments instead of hard-failing.
pytestmark = pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc is not installed")

ADAPTER_SDP = (
    "v=0\r\n"
    "o=- 4611730409192982894 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=ice-ufrag:abc123\r\n"
    "a=ice-pwd:xyz789\r\n"
    "a=candidate:found1 1 udp 2113937151 192.168.1.5 52100 typ host\r\n"
    "a=candidate:found2 2 udp 2113937151 192.168.1.5 52101 typ srflx\r\n"
    "a=fingerprint:sha-256 1A:2B:3C:4D:5E:6F:70:11:22:33:44:55:66:77:88:99\r\n"
    "a=fingerprint:sha-1 12:34:56:78:9A:BC:DE:F0:12:34:56:78\r\n"
)

NO_ICE_SDP = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"


class FakeLocalDescription:
    def __init__(self, sdp):
        self.sdp = sdp


class FakeTrack:
    def __init__(self, kind, label="fake-track"):
        self.kind = kind
        self.label = label
        self.readyState = "live"


class FakeReceiver:
    def __init__(self, track):
        self.track = track


class FakeSender:
    def __init__(self, track):
        self.track = track


class FakePC:
    """A controllable stand-in for aiortc's RTCPeerConnection."""

    def __init__(
        self,
        sdp=ADAPTER_SDP,
        ice_gathering_state="complete",
        remote_description=None,
        auto_gather=False,
        signaling_state="stable",
    ):
        self._handlers = {}
        self.localDescription = None
        self.remoteDescription = remote_description
        self.iceGatheringState = ice_gathering_state
        # A real pc starts "stable"; ``create_transport`` only builds a local
        # offer from that state (or "new"), so a pending remote offer can be
        # reported as a diagnosable error rather than an InvalidStateError.
        self.signalingState = signaling_state
        self._sdp = sdp
        self.auto_gather = auto_gather
        self.data_channels = []
        self.added_tracks = []
        self.receivers = []
        self.senders = []
        self.closed = False
        self.offers_created = 0

    def on(self, event, handler=None):
        if handler is None:

            def deco(fn):
                self._handlers[event] = fn
                return fn

            return deco
        self._handlers[event] = handler
        return handler

    def _fire(self, event, *args):
        handler = self._handlers.get(event)
        if handler is not None:
            return handler(*args)
        return None

    def createDataChannel(self, label):
        self.data_channels.append(label)
        return Mock()

    async def createOffer(self):
        self.offers_created += 1
        return FakeLocalDescription(self._sdp)

    async def setLocalDescription(self, desc):
        self.localDescription = desc
        if self.auto_gather and self.iceGatheringState == "gathering":
            self.iceGatheringState = "complete"
            self._fire("icegatheringstatechange")

    def addTrack(self, track):
        self.added_tracks.append(track)
        return Mock()

    def getReceivers(self):
        return self.receivers

    def getSenders(self):
        return self.senders

    async def close(self):
        self.closed = True


class FakeRecorder:
    def __init__(self, filepath):
        self.filepath = filepath
        self.added = []
        self.started = False
        self.stopped = False

    def addTrack(self, track):
        self.added.append(track)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _stub_peer_connection(monkeypatch):
    """Route every RTCPeerConnection construction through FakePC."""
    monkeypatch.setattr(aiortc_adapter, "RTCPeerConnection", FakePC)


@pytest.fixture
def adapter():
    return AiortcAdapter(ice_servers=[{"urls": "stun:stun.example.com:3478"}])


@pytest.fixture
def tracking_recorders(monkeypatch):
    recorders = {}

    def factory(filepath):
        rec = FakeRecorder(filepath)
        recorders[filepath] = rec
        return rec

    monkeypatch.setattr(aiortc_adapter, "MediaRecorder", factory)
    return recorders


# ----------------------------------------------------------------------
# SDP parsing helpers
# ----------------------------------------------------------------------


class TestParseIceParams:
    def test_parses_ufrag_and_pwd(self):
        params = _parse_ice_params_from_sdp(ADAPTER_SDP)
        assert params == {"usernameFragment": "abc123", "password": "xyz789"}

    def test_missing_credentials_yield_empty_strings(self):
        params = _parse_ice_params_from_sdp(NO_ICE_SDP)
        assert params == {"usernameFragment": "", "password": ""}

    def test_empty_sdp_yields_empty_strings(self):
        assert _parse_ice_params_from_sdp("") == {
            "usernameFragment": "",
            "password": "",
        }


class TestParseIceCandidates:
    def test_parses_all_candidates_with_typed_fields(self):
        candidates = _parse_ice_candidates_from_sdp(ADAPTER_SDP)
        assert len(candidates) == 2
        first = candidates[0]
        assert first == {
            "foundation": "found1",
            "component": 1,
            "protocol": "udp",
            "priority": 2113937151,
            "ip": "192.168.1.5",
            "port": 52100,
            "type": "host",
        }
        assert candidates[1]["type"] == "srflx"
        assert candidates[1]["component"] == 2

    def test_no_candidates_returns_empty_list(self):
        assert _parse_ice_candidates_from_sdp(NO_ICE_SDP) == []

    def test_malformed_candidate_lines_are_skipped(self):
        sdp = "a=candidate:partial 1 udp 100\n" + ADAPTER_SDP
        candidates = _parse_ice_candidates_from_sdp(sdp)
        # Only the two well-formed lines survive.
        assert len(candidates) == 2


class TestParseDtlsParams:
    def test_parses_all_fingerprints(self):
        params = _parse_dtls_params_from_sdp(ADAPTER_SDP)
        assert params["role"] == "auto"
        assert params["fingerprints"] == [
            {
                "algorithm": "sha-256",
                "value": "1A:2B:3C:4D:5E:6F:70:11:22:33:44:55:66:77:88:99",
            },
            {"algorithm": "sha-1", "value": "12:34:56:78:9A:BC:DE:F0:12:34:56:78"},
        ]

    def test_no_fingerprints_returns_empty_list(self):
        assert _parse_dtls_params_from_sdp(NO_ICE_SDP) == {
            "fingerprints": [],
            "role": "auto",
        }


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


class TestConstruction:
    def test_requires_aiortc(self, monkeypatch):
        monkeypatch.setattr(aiortc_adapter, "AIORTC_AVAILABLE", False)
        with pytest.raises(SFUConnectionError, match="aiortc is not installed"):
            AiortcAdapter()

    def test_initialises_empty_state(self):
        adapter = AiortcAdapter()
        assert adapter._rooms == {}
        assert adapter._recordings == {}
        assert adapter._ice_servers == []

    def test_stores_ice_servers(self):
        servers = [{"urls": "stun:stun.example.com:3478"}]
        assert AiortcAdapter(ice_servers=servers)._ice_servers == servers

    def test_default_acoustic_defense_disabled(self):
        adapter = AiortcAdapter()
        assert adapter._acoustic_defense.enabled is False


# ----------------------------------------------------------------------
# Room lifecycle
# ----------------------------------------------------------------------


class TestRoomLifecycle:
    @pytest.mark.asyncio
    async def test_create_room_returns_empty_room_info(self):
        room = await AiortcAdapter().create_room("room-1")
        assert isinstance(room, RoomInfo)
        assert room.id == "room-1"
        assert room.peers == []
        assert room.producers == []

    @pytest.mark.asyncio
    async def test_create_room_twice_returns_same_room(self):
        adapter = AiortcAdapter()
        first = await adapter.create_room("room-dup")
        second = await adapter.create_room("room-dup")
        assert first.id == second.id == "room-dup"
        assert len(adapter._rooms) == 1

    @pytest.mark.asyncio
    async def test_close_room_missing_returns_false(self):
        assert await AiortcAdapter().close_room("missing") is False

    @pytest.mark.asyncio
    async def test_close_room_closes_peers_and_removes_room(self):
        adapter = AiortcAdapter()
        await adapter.create_room("room-x")
        await adapter.join_room("room-x", "peer-1")
        peer = adapter._rooms["room-x"]["peer-1"]
        assert await adapter.close_room("room-x") is True
        assert peer.pc.closed is True
        assert "room-x" not in adapter._rooms

    @pytest.mark.asyncio
    async def test_join_room_auto_creates_room(self):
        adapter = AiortcAdapter()
        caps = await adapter.join_room("room-auto", "peer-1")
        assert "room-auto" in adapter._rooms
        assert caps["codecs"][0]["mimeType"] == "audio/opus"

    @pytest.mark.asyncio
    async def test_join_room_returns_capabilities_with_all_codecs(self):
        caps = await AiortcAdapter().join_room("room-caps", "peer-1")
        mimes = {c["mimeType"] for c in caps["codecs"]}
        assert mimes == {
            "audio/opus",
            "video/VP8",
            "video/VP9",
            "video/H264",
        }

    @pytest.mark.asyncio
    async def test_join_room_creates_pc_with_track_handlers(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-h", "peer-1")
        peer = adapter._rooms["room-h"]["peer-1"]
        assert "track" in peer.pc._handlers
        assert "negotiationneeded" in peer.pc._handlers

    @pytest.mark.asyncio
    async def test_join_room_second_time_returns_capabilities(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-re", "peer-1")
        caps = await adapter.join_room("room-re", "peer-1")
        assert caps["codecs"][0]["mimeType"] == "audio/opus"

    @pytest.mark.asyncio
    async def test_on_track_stores_audio_and_video_tracks(self):
        adapter = AiortcAdapter(acoustic_defense=AcousticDefenseConfig(enabled=True))
        await adapter.join_room("room-tr", "peer-1")
        peer = adapter._rooms["room-tr"]["peer-1"]
        audio = FakeTrack("audio")
        await peer.pc._fire("track", audio)
        assert len(peer.incoming_audio_tracks) == 1
        # Acoustic defense enabled -> audio is wrapped.
        assert isinstance(peer.incoming_audio_tracks[0], AudioProcessingTrack)
        video = FakeTrack("video")
        await peer.pc._fire("track", video)
        assert peer.incoming_video_tracks == [video]

    @pytest.mark.asyncio
    async def test_on_track_without_acoustic_defense_keeps_raw_audio(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-raw", "peer-1")
        peer = adapter._rooms["room-raw"]["peer-1"]
        audio = FakeTrack("audio")
        await peer.pc._fire("track", audio)
        assert peer.incoming_audio_tracks == [audio]

    @pytest.mark.asyncio
    async def test_join_room_forwards_existing_producer_tracks(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-fwd", "peer-1")
        first = adapter._rooms["room-fwd"]["peer-1"]
        first.producer_tracks["prod-1"] = FakeTrack("audio")
        await adapter.join_room("room-fwd", "peer-2")
        second = adapter._rooms["room-fwd"]["peer-2"]
        assert len(second.pc.added_tracks) == 1

    @pytest.mark.asyncio
    async def test_leave_room_missing_room_returns_false(self):
        assert await AiortcAdapter().leave_room("nope", "peer-1") is False

    @pytest.mark.asyncio
    async def test_leave_room_missing_peer_returns_false(self):
        adapter = AiortcAdapter()
        await adapter.create_room("room-l")
        assert await adapter.leave_room("room-l", "ghost") is False

    @pytest.mark.asyncio
    async def test_leave_room_closes_peer_and_removes(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-l2", "peer-1")
        peer = adapter._rooms["room-l2"]["peer-1"]
        # Join a second peer so the room survives the first leave.
        await adapter.join_room("room-l2", "peer-2")
        assert await adapter.leave_room("room-l2", "peer-1") is True
        assert peer.pc.closed is True
        assert "peer-1" not in adapter._rooms["room-l2"]
        assert "peer-2" in adapter._rooms["room-l2"]

    @pytest.mark.asyncio
    async def test_leave_room_auto_closes_empty_room(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-l3", "peer-1")
        await adapter.leave_room("room-l3", "peer-1")
        assert "room-l3" not in adapter._rooms


# ----------------------------------------------------------------------
# Transports
# ----------------------------------------------------------------------


class TestTransports:
    @pytest.mark.asyncio
    async def test_create_transport_unknown_peer_raises(self):
        adapter = AiortcAdapter()
        await adapter.create_room("room-t")
        with pytest.raises(SFUConnectionError, match="not in room"):
            await adapter.create_transport("room-t", "ghost", TransportDirection.SEND)

    @pytest.mark.asyncio
    async def test_create_transport_parses_sdp_and_stores(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-t2", "peer-1")
        transport = await adapter.create_transport(
            "room-t2", "peer-1", TransportDirection.SEND
        )
        assert isinstance(transport, SFUTransport)
        assert transport.direction == TransportDirection.SEND
        assert transport.ice_parameters == {
            "usernameFragment": "abc123",
            "password": "xyz789",
        }
        assert len(transport.ice_candidates) == 2
        assert transport.dtls_parameters["fingerprints"][0]["algorithm"] == "sha-256"
        peer = adapter._rooms["room-t2"]["peer-1"]
        assert transport.id in peer.transports

    @pytest.mark.asyncio
    async def test_create_transport_creates_data_channel_once(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-t3", "peer-1")
        await adapter.create_transport("room-t3", "peer-1", TransportDirection.RECV)
        peer = adapter._rooms["room-t3"]["peer-1"]
        assert len(peer.pc.data_channels) == 1
        assert peer.pc.data_channels[0].startswith("transport-init-")

    @pytest.mark.asyncio
    async def test_create_transport_completes_ice_gathering(self):
        adapter = AiortcAdapter()
        # Gathering starts in "gathering"; setLocalDescription flips it and fires.
        await adapter.join_room("room-t4", "peer-1")
        peer = adapter._rooms["room-t4"]["peer-1"]
        peer.pc.iceGatheringState = "gathering"
        peer.pc.auto_gather = True
        transport = await adapter.create_transport(
            "room-t4", "peer-1", TransportDirection.SEND
        )
        assert transport.ice_parameters["usernameFragment"] == "abc123"

    @pytest.mark.asyncio
    async def test_connect_transport_unknown_room_returns_false(self):
        adapter = AiortcAdapter()
        assert await adapter.connect_transport("room-x", "p", "t", {}) is False

    @pytest.mark.asyncio
    async def test_connect_transport_unknown_transport_returns_false(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-c", "peer-1")
        assert (
            await adapter.connect_transport("room-c", "peer-1", "t-missing", {})
            is False
        )

    @pytest.mark.asyncio
    async def test_connect_transport_stores_dtls_parameters(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-c2", "peer-1")
        transport = await adapter.create_transport(
            "room-c2", "peer-1", TransportDirection.SEND
        )
        dtls = {"fingerprints": [{"algorithm": "sha-256", "value": "AA:BB"}]}
        assert (
            await adapter.connect_transport("room-c2", "peer-1", transport.id, dtls)
            is True
        )
        peer = adapter._rooms["room-c2"]["peer-1"]
        assert peer.transports[transport.id].dtls_parameters == dtls


# ----------------------------------------------------------------------
# Produce / consume
# ----------------------------------------------------------------------


class TestProduceConsume:
    @pytest.mark.asyncio
    async def test_produce_unknown_peer_raises(self):
        adapter = AiortcAdapter()
        await adapter.create_room("room-p")
        with pytest.raises(SFUConnectionError, match="not in room"):
            await adapter.produce("room-p", "ghost", "t", MediaKind.AUDIO, {})

    @pytest.mark.asyncio
    async def test_produce_without_track_still_creates_producer(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-p2", "peer-1")
        producer = await adapter.produce(
            "room-p2", "peer-1", "t", MediaKind.AUDIO, {"codec": "opus"}
        )
        assert producer.kind == MediaKind.AUDIO
        assert producer.paused is False
        assert producer.rtp_parameters == {"codec": "opus"}
        peer = adapter._rooms["room-p2"]["peer-1"]
        assert producer.id in peer.producers

    @pytest.mark.asyncio
    async def test_produce_finds_track_from_receivers(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-p3", "peer-1")
        peer = adapter._rooms["room-p3"]["peer-1"]
        peer.pc.receivers = [FakeReceiver(FakeTrack("video"))]
        producer = await adapter.produce("room-p3", "peer-1", "t", MediaKind.VIDEO, {})
        assert peer.producer_tracks[producer.id].kind == "video"

    @pytest.mark.asyncio
    async def test_produce_forwards_track_to_other_peers(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-p4", "peer-1")
        await adapter.join_room("room-p4", "peer-2")
        first = adapter._rooms["room-p4"]["peer-1"]
        first.incoming_audio_tracks.append(FakeTrack("audio"))
        await adapter.produce("room-p4", "peer-1", "t", MediaKind.AUDIO, {})
        second = adapter._rooms["room-p4"]["peer-2"]
        assert len(second.pc.added_tracks) == 1

    @pytest.mark.asyncio
    async def test_produce_renegotiates_when_remote_description_set(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-p5", "peer-1")
        await adapter.join_room("room-p5", "peer-2")
        first = adapter._rooms["room-p5"]["peer-1"]
        first.incoming_audio_tracks.append(FakeTrack("audio"))
        second = adapter._rooms["room-p5"]["peer-2"]
        second.pc.remoteDescription = FakeLocalDescription(ADAPTER_SDP)
        await adapter.produce("room-p5", "peer-1", "t", MediaKind.AUDIO, {})
        assert second.pc.offers_created == 1

    @pytest.mark.asyncio
    async def test_produce_audio_wraps_in_acoustic_defense(self):
        adapter = AiortcAdapter(acoustic_defense=AcousticDefenseConfig(enabled=True))
        await adapter.join_room("room-p6", "peer-1")
        peer = adapter._rooms["room-p6"]["peer-1"]
        peer.incoming_audio_tracks.append(FakeTrack("audio"))
        await adapter.produce("room-p6", "peer-1", "t", MediaKind.AUDIO, {})
        assert isinstance(
            peer.producer_tracks[list(peer.producer_tracks)[0]], AudioProcessingTrack
        )

    @pytest.mark.asyncio
    async def test_consume_unknown_peer_raises(self):
        adapter = AiortcAdapter()
        await adapter.create_room("room-c")
        with pytest.raises(SFUConnectionError, match="not in room"):
            await adapter.consume("room-c", "ghost", "t", "prod-1", {})

    @pytest.mark.asyncio
    async def test_consume_unknown_producer_raises(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-c2", "peer-1")
        with pytest.raises(SFUConnectionError, match="Producer prod-1 not found"):
            await adapter.consume("room-c2", "peer-1", "t", "prod-1", {})

    @pytest.mark.asyncio
    async def test_consume_wires_consumer_to_producer_kind(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-c3", "peer-1")
        producer = await adapter.produce("room-c3", "peer-1", "t", MediaKind.VIDEO, {})
        consumer = await adapter.consume(
            "room-c3", "peer-1", "t", producer.id, {"codecs": []}
        )
        assert consumer.producer_id == producer.id
        assert consumer.kind == MediaKind.VIDEO
        assert consumer.rtp_parameters == {"codecs": []}
        peer = adapter._rooms["room-c3"]["peer-1"]
        assert consumer.id in peer.consumers

    @pytest.mark.asyncio
    async def test_consume_adds_track_to_peer(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-c4", "peer-1")
        await adapter.join_room("room-c4", "peer-2")
        first = adapter._rooms["room-c4"]["peer-1"]
        first.incoming_audio_tracks.append(FakeTrack("audio"))
        producer = await adapter.produce("room-c4", "peer-1", "t", MediaKind.AUDIO, {})
        second = adapter._rooms["room-c4"]["peer-2"]
        # Produce already forwarded the track; isolate the consume wiring.
        second.pc.added_tracks.clear()
        second.pc.senders = [FakeSender(None)]
        await adapter.consume("room-c4", "peer-2", "t", producer.id, {})
        assert len(second.pc.added_tracks) == 1

    @pytest.mark.asyncio
    async def test_consume_skips_duplicate_sender_wiring(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-c5", "peer-1")
        await adapter.join_room("room-c5", "peer-2")
        first = adapter._rooms["room-c5"]["peer-1"]
        track = FakeTrack("audio")
        first.incoming_audio_tracks.append(track)
        producer = await adapter.produce("room-c5", "peer-1", "t", MediaKind.AUDIO, {})
        second = adapter._rooms["room-c5"]["peer-2"]
        # Produce already forwarded the track; isolate the consume wiring.
        second.pc.added_tracks.clear()
        second.pc.senders = [FakeSender(track)]
        await adapter.consume("room-c5", "peer-2", "t", producer.id, {})
        # Already wired -> no additional addTrack.
        assert len(second.pc.added_tracks) == 0


class TestProducerControls:
    @pytest.mark.asyncio
    async def test_pause_unknown_room_or_producer_returns_false(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-x", "peer-1")
        assert await adapter.pause_producer("room-x", "peer-1", "nope") is False
        assert await adapter.pause_producer("room-y", "peer-1", "nope") is False

    @pytest.mark.asyncio
    async def test_pause_and_resume_toggle_flag(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-ctrl", "peer-1")
        producer = await adapter.produce(
            "room-ctrl", "peer-1", "t", MediaKind.AUDIO, {}
        )
        assert await adapter.pause_producer("room-ctrl", "peer-1", producer.id) is True
        peer = adapter._rooms["room-ctrl"]["peer-1"]
        assert peer.producers[producer.id].paused is True
        assert await adapter.resume_producer("room-ctrl", "peer-1", producer.id) is True
        assert peer.producers[producer.id].paused is False

    @pytest.mark.asyncio
    async def test_close_producer_removes_state(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-close", "peer-1")
        producer = await adapter.produce(
            "room-close", "peer-1", "t", MediaKind.AUDIO, {}
        )
        peer = adapter._rooms["room-close"]["peer-1"]
        assert await adapter.close_producer("room-close", "peer-1", producer.id) is True
        assert producer.id not in peer.producers
        assert producer.id not in peer.producer_tracks

    @pytest.mark.asyncio
    async def test_close_producer_unknown_returns_false(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-close2", "peer-1")
        assert await adapter.close_producer("room-close2", "peer-1", "ghost") is False


# ----------------------------------------------------------------------
# Room info / capabilities
# ----------------------------------------------------------------------


class TestRoomInfo:
    @pytest.mark.asyncio
    async def test_get_room_info_missing_returns_none(self):
        assert await AiortcAdapter().get_room_info("missing") is None

    @pytest.mark.asyncio
    async def test_get_room_info_lists_peers_and_producers(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-info", "peer-1")
        await adapter.join_room("room-info", "peer-2")
        await adapter.produce("room-info", "peer-1", "t", MediaKind.AUDIO, {})
        info = await adapter.get_room_info("room-info")
        assert sorted(info.peers) == ["peer-1", "peer-2"]
        assert len(info.producers) == 1

    @pytest.mark.asyncio
    async def test_set_preferred_layers_reports_not_applied(self):
        """Nothing to change (that peer is not in a room) reports False.

        ``BandwidthEstimator`` logs a degradation as applied on a True result,
        so a no-op must never claim success. The positive path -- suspending a
        real video sender -- is covered in
        ``test_sfu_aiortc_layer_hint_apply.py``.
        """
        adapter = AiortcAdapter()
        assert await adapter.set_preferred_layers("r", "p", "c", 0, 1) is False

    @pytest.mark.asyncio
    async def test_health_check_reports_aiortc_availability(self):
        adapter = AiortcAdapter()
        assert await adapter.health_check() is AIORTC_AVAILABLE
        assert AIORTC_AVAILABLE is True

    def test_adapter_close_idempotent(self):
        assert AIORTC_AVAILABLE is True


# ----------------------------------------------------------------------
# Recording
# ----------------------------------------------------------------------


class TestRecording:
    @pytest.mark.asyncio
    async def test_start_recording_missing_room_raises(self):
        adapter = AiortcAdapter()
        with pytest.raises(SFUConnectionError, match="Room missing does not exist"):
            await adapter.start_recording("missing", "/tmp/out")

    @pytest.mark.asyncio
    async def test_start_recording_returns_existing_recording_id(
        self, tracking_recorders
    ):
        adapter = AiortcAdapter()
        await adapter.join_room("room-rec", "peer-1")
        first = await adapter.start_recording("room-rec", "/tmp/out")
        second = await adapter.start_recording("room-rec", "/tmp/out")
        assert second == {"recording_id": first["recording_id"]}
        assert first["file_count"] == 0

    @pytest.mark.asyncio
    async def test_start_recording_records_existing_audio_tracks(
        self, tracking_recorders, tmp_path
    ):
        adapter = AiortcAdapter()
        await adapter.join_room("room-rec2", "peer-1")
        peer = adapter._rooms["room-rec2"]["peer-1"]
        peer.incoming_audio_tracks.append(FakeTrack("audio"))
        peer.incoming_audio_tracks.append(FakeTrack("audio"))
        result = await adapter.start_recording("room-rec2", str(tmp_path))
        assert result["file_count"] == 2
        assert len(tracking_recorders) == 2
        for recorder in tracking_recorders.values():
            assert recorder.started is True
            assert len(recorder.added) == 1

    @pytest.mark.asyncio
    async def test_start_recording_skips_failed_recorders(
        self, tracking_recorders, monkeypatch, tmp_path
    ):
        adapter = AiortcAdapter()
        await adapter.join_room("room-rec3", "peer-1")
        peer = adapter._rooms["room-rec3"]["peer-1"]
        peer.incoming_audio_tracks.append(FakeTrack("audio"))

        async def boom(self):
            raise RuntimeError("recorder failed")

        monkeypatch.setattr(FakeRecorder, "start", boom)
        result = await adapter.start_recording("room-rec3", str(tmp_path))
        assert result["file_count"] == 0

    @pytest.mark.asyncio
    async def test_stop_recording_not_recording_returns_none(self):
        adapter = AiortcAdapter()
        await adapter.join_room("room-nr", "peer-1")
        assert await adapter.stop_recording("room-nr") is None

    @pytest.mark.asyncio
    async def test_stop_recording_returns_file_paths(
        self, tracking_recorders, tmp_path
    ):
        adapter = AiortcAdapter()
        await adapter.join_room("room-rec4", "peer-1")
        peer = adapter._rooms["room-rec4"]["peer-1"]
        peer.incoming_audio_tracks.append(FakeTrack("audio"))
        await adapter.start_recording("room-rec4", str(tmp_path))
        filepaths = await adapter.stop_recording("room-rec4")
        assert filepaths is not None
        assert len(filepaths) == 1
        assert filepaths[0].endswith(".webm")
        assert os.path.exists(filepaths[0]) is False  # no real files written
        assert "room-rec4" not in adapter._recordings
        recorder = next(iter(tracking_recorders.values()))
        assert recorder.stopped is True

    @pytest.mark.asyncio
    async def test_close_room_stops_active_recording(
        self, tracking_recorders, tmp_path
    ):
        adapter = AiortcAdapter()
        await adapter.join_room("room-rec5", "peer-1")
        peer = adapter._rooms["room-rec5"]["peer-1"]
        peer.incoming_audio_tracks.append(FakeTrack("audio"))
        await adapter.start_recording("room-rec5", str(tmp_path))
        assert await adapter.close_room("room-rec5") is True
        assert "room-rec5" not in adapter._recordings

    @pytest.mark.asyncio
    async def test_close_stops_recordings_and_closes_rooms(
        self, tracking_recorders, tmp_path
    ):
        adapter = AiortcAdapter()
        await adapter.join_room("room-rec6", "peer-1")
        await adapter.join_room("room-rec6", "peer-2")
        await adapter.close()
        assert adapter._rooms == {}
        assert adapter._recordings == {}
