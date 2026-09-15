"""
aiortc SFU adapter - Pure Python WebRTC SFU implementation.

This adapter uses aiortc to implement an SFU directly in Python,
eliminating the need for external SFU services like mediasoup or Janus.
It runs in-process with the FastAPI application for maximum integration.
"""

import asyncio
import os
import re
import time
import uuid
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

import utils.logger as logger
from .base import (
    SFUAdapter,
    RoomInfo,
    SFUTransport,
    SFUProducer,
    SFUConsumer,
    TransportDirection,
    MediaKind,
)
from .quality import QualityReporter, parse_quality_metrics
from .acoustic_defense import (
    AcousticDefenseConfig,
    AudioProcessingTrack,
)
from ..exceptions import SFUConnectionError


RTCPeerConnection: Any = None
AIORTC_AVAILABLE = False
try:
    from aiortc import RTCPeerConnection

    AIORTC_AVAILABLE = True
except ImportError:
    logger.warning("aiortc not installed - SFU adapter will not be available")

MediaRecorder: Any = None
AIORTC_RECORDING_AVAILABLE = False
try:
    from aiortc.contrib.media import MediaRecorder

    AIORTC_RECORDING_AVAILABLE = True
except ImportError:
    logger.warning("aiortc MediaRecorder not available - recording will be disabled")


def _parse_ice_params_from_sdp(sdp: str) -> Dict[str, str]:
    ufrag_m = re.search(r"a=ice-ufrag:(\S+)", sdp)
    pwd_m = re.search(r"a=ice-pwd:(\S+)", sdp)
    return {
        "usernameFragment": ufrag_m.group(1) if ufrag_m else "",
        "password": pwd_m.group(1) if pwd_m else "",
    }


def _parse_ice_candidates_from_sdp(sdp: str) -> List[Dict[str, Any]]:
    candidates = []
    for m in re.finditer(
        r"a=candidate:(\S+) (\d+) (\S+) (\d+) (\S+) (\d+) typ (\S+)",
        sdp,
    ):
        candidates.append(
            {
                "foundation": m.group(1),
                "component": int(m.group(2)),
                "protocol": m.group(3),
                "priority": int(m.group(4)),
                "ip": m.group(5),
                "port": int(m.group(6)),
                "type": m.group(7),
            }
        )
    return candidates


# aiortc reports RTP jitter in the codec's clock-rate units
# (``StreamStatistics.jitter`` accumulates ``arrival - rtp_timestamp``
# differences where arrival is scaled by the stream clock rate). The quality
# store and BandwidthEstimator measure jitter in milliseconds, so the raw
# value must be divided by the codec clock rate. Dividing is also the safe
# direction: an unexpected unit can only under-report, never fabricate a
# degradation.
_CLOCK_RATES = {"audio": 48000, "video": 90000}
_DEFAULT_CLOCK_RATE = 48000


def _as_number(value: Any) -> Optional[float]:
    """Coerce a stat field to a finite float, or ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _quality_metrics_from_stats(records: Any) -> Dict[str, Any]:
    """Map aiortc ``pc.getStats()`` records to canonical quality metrics.

    ``remote-inbound-rtp`` records are the remote endpoint's report about the
    stream the SFU sends it -- exactly the downlink signal BandwidthEstimator
    needs -- and are preferred. ``inbound-rtp`` (what the SFU receives) is the
    fallback while no RTCP report has arrived yet. Records with nothing
    measurable are skipped rather than guessed at, and all values go through
    the shared alias mapper so this adapter reports the same canonical keys as
    the janus and mediasoup adapters.
    """
    buckets: Dict[str, Dict[str, Any]] = {
        "remote-inbound-rtp": {
            "seen": False,
            "lost": 0,
            "received": 0,
            "jitter_ms": None,
            "rtt_ms": None,
        },
        "inbound-rtp": {
            "seen": False,
            "lost": 0,
            "received": 0,
            "jitter_ms": None,
            "rtt_ms": None,
        },
    }
    for record in records or ():
        stat_type = getattr(record, "type", None)
        if stat_type not in buckets:
            continue
        bucket = buckets[stat_type]
        bucket["seen"] = True
        lost = _as_number(getattr(record, "packetsLost", None))
        received = _as_number(getattr(record, "packetsReceived", None))
        if lost is not None and lost > 0:
            bucket["lost"] += int(lost)
        if received is not None and received > 0:
            bucket["received"] += int(received)

        kind = str(getattr(record, "kind", "") or "").lower()
        clock = _CLOCK_RATES.get(kind, _DEFAULT_CLOCK_RATE)
        jitter = _as_number(getattr(record, "jitter", None))
        if jitter is not None and jitter >= 0:
            jitter_ms = jitter * 1000.0 / clock
            current = bucket["jitter_ms"]
            bucket["jitter_ms"] = (
                jitter_ms if current is None else max(current, jitter_ms)
            )
        rtt = _as_number(getattr(record, "roundTripTime", None))
        if rtt is not None and rtt >= 0:
            # RTCP round-trip times are seconds.
            rtt_ms = rtt * 1000.0
            current_rtt = bucket["rtt_ms"]
            bucket["rtt_ms"] = (
                rtt_ms if current_rtt is None else max(current_rtt, rtt_ms)
            )

    remote = buckets["remote-inbound-rtp"]
    bucket = remote if remote["seen"] else buckets["inbound-rtp"]
    if not bucket["seen"]:
        return {}

    raw: Dict[str, Any] = {}
    total = bucket["received"] + bucket["lost"]
    if total > 0:
        raw["packetLoss"] = bucket["lost"] * 100.0 / total
    if bucket["jitter_ms"] is not None:
        raw["jitter"] = bucket["jitter_ms"]
    if bucket["rtt_ms"] is not None:
        raw["roundTripTime"] = bucket["rtt_ms"]
    return parse_quality_metrics(raw)


def _total_bytes_sent(records: Any) -> Optional[int]:
    """Sum the ``outbound-rtp`` byte counters of one peer's pc, if present."""
    total = 0
    seen = False
    for record in records or ():
        if getattr(record, "type", None) != "outbound-rtp":
            continue
        value = _as_number(getattr(record, "bytesSent", None))
        if value is None or value < 0:
            continue
        total += int(value)
        seen = True
    return total if seen else None


def _parse_dtls_params_from_sdp(sdp: str) -> Dict[str, Any]:
    fingerprints = []
    for m in re.finditer(r"a=fingerprint:(\S+) (\S+)", sdp):
        fingerprints.append(
            {
                "algorithm": m.group(1),
                "value": m.group(2),
            }
        )
    return {
        "fingerprints": fingerprints,
        "role": "auto",
    }


# Frame-rate ceiling applied to a peer whose downlink the quality estimator
# has flagged as congested. aiortc negotiates a single video encoding and
# exposes no encoder or bitrate API, so the frame rate is the only lever that
# actually bounds what the sender emits.
DEGRADED_VIDEO_FPS = 5.0


class BoundedVideoTrack:
    """A frame-rate bounded view of a video track, for a degraded peer.

    aiortc 1.15's ``RTCRtpSender`` exposes no ``getParameters`` /
    ``setParameters`` and no ``maxBitrate``, so ``replaceTrack`` is the only
    runtime lever over a live sender. Replacing a peer's outbound video with
    this wrapper is the nearest thing to dropping to a lower simulcast layer:
    the peer keeps receiving video -- at the bounded rate, rather than a torn
    down stream or a black picture -- while the frames the sender has to
    encode, and therefore the bitrate, fall.

    Frames that are not forwarded are still read from the source and dropped,
    so the source queue cannot accumulate; aiortc's own disabled-sender path
    reads and discards for the same reason.

    ``stop()`` deliberately does NOT stop the wrapped source: that same track
    is forwarded to every other peer in the room, so a sender ending must not
    end video for the rest of the room.
    """

    def __init__(self, source: Any, max_fps: float = DEGRADED_VIDEO_FPS) -> None:
        self._source = source
        self._min_interval = 1.0 / max(1.0, float(max_fps))
        self._last_sent = 0.0
        self._ended = False
        self._id = str(uuid.uuid4())
        self._enabled = True

    @property
    def source(self) -> Any:
        """The bounded track itself; what a restore re-attaches."""
        return self._source

    @property
    def kind(self) -> str:
        return getattr(self._source, "kind", "video")

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return getattr(self._source, "label", "")

    @property
    def readyState(self) -> str:
        return "ended" if self._ended else "live"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def stop(self) -> None:
        """End this view only -- never the shared track underneath it."""
        self._ended = True

    async def recv(self) -> Any:
        """Return the next frame the bounded rate allows, dropping the rest."""
        while not self._ended:
            frame = await self._source.recv()
            if frame is None:
                return None
            now = time.monotonic()
            if now - self._last_sent >= self._min_interval:
                self._last_sent = now
                return frame
        return None


def _sender_view(track: Any) -> Any:
    """The instance to hand one sender for ``track``.

    An acoustic-defense wrapper is shared by the whole room, but aiortc stops
    a sender's track when its RTP task exits. Each sender therefore gets its
    own view of a wrapped audio track; raw and bounded-video tracks are handed
    over as-is.
    """
    if isinstance(track, AudioProcessingTrack):
        return track.clone()
    return track


def _same_sender_track(a: Any, b: Any) -> bool:
    """Whether ``a`` and ``b`` are the same produced track.

    A per-sender audio view carries the ``share_key`` of the producer wrapper
    it was cloned from, so a ghost view is found even though each sender holds
    a distinct instance. Unwrapped tracks compare by identity.
    """
    if a is b:
        return True
    key = getattr(a, "share_key", None)
    return key is not None and key == getattr(b, "share_key", None)


@dataclass
class AiortcPeer:
    """A peer in the aiortc SFU."""

    peer_id: str
    pc: Any
    producers: Dict[str, SFUProducer] = field(default_factory=dict)
    consumers: Dict[str, SFUConsumer] = field(default_factory=dict)
    transports: Dict[str, SFUTransport] = field(default_factory=dict)
    incoming_audio_tracks: List[Any] = field(default_factory=list)
    incoming_video_tracks: List[Any] = field(default_factory=list)
    producer_tracks: Dict[str, Any] = field(default_factory=dict)
    # Senders whose outbound video a degradation hint replaced with a
    # frame-rate bounded view, mapped back to the source track that must be
    # re-attached to restore full rate.
    degraded_senders: Dict[Any, Any] = field(default_factory=dict)


class AiortcAdapter(SFUAdapter):
    """
    aiortc-based SFU adapter.

    This adapter implements an SFU using aiortc directly in Python.
    It manages peer connections and routes media streams between peers.
    """

    def __init__(
        self,
        ice_servers: Optional[List[Dict[str, Any]]] = None,
        acoustic_defense: Optional[AcousticDefenseConfig] = None,
    ):
        """
        Initialize the aiortc SFU adapter.

        Args:
            ice_servers: List of STUN/TURN server configurations in aiortc format
                [{"urls": "stun:..."}, {"urls": "turn:...", "username": "...", "credential": "..."}]
            acoustic_defense: Optional acoustic eavesdropping defense configuration
        """
        if not AIORTC_AVAILABLE:
            raise SFUConnectionError(
                "aiortc is not installed. Install with: pip install aiortc==1.14.0"
            )

        self._rooms: Dict[str, Dict[str, AiortcPeer]] = {}
        self._ice_servers = ice_servers or []
        self._acoustic_defense = acoustic_defense or AcousticDefenseConfig()
        self._recordings: Dict[str, Dict[str, Any]] = {}
        # Quality bridge state (see ``collect_quality``).
        self._quality_mixin: Any = None
        self._quality_reporter: Optional[QualityReporter] = None
        # Last ``(bytesSent, monotonic ts)`` per peer, for bitrate deltas.
        self._last_quality_sample: Dict[str, Tuple[int, float]] = {}
        if AIORTC_RECORDING_AVAILABLE:
            logger.info("aiortc MediaRecorder available for call recording")
        else:
            logger.info("aiortc MediaRecorder not available - call recording disabled")
        logger.info("aiortc SFU adapter initialized")

    # ------------------------------------------------------------------
    # Room lifecycle
    # ------------------------------------------------------------------

    async def create_room(self, room_id: str) -> RoomInfo:
        """Create a new room."""
        if room_id in self._rooms:
            logger.warning(f"Room {room_id} already exists, returning existing")
            room_info = await self.get_room_info(room_id)
            if room_info is None:
                raise SFUConnectionError(f"Failed to get room info for {room_id}")
            return room_info

        self._rooms[room_id] = {}
        logger.info(f"Created aiortc SFU room: {room_id}")
        return RoomInfo(id=room_id, peers=[], producers=[])

    async def close_room(self, room_id: str) -> bool:
        """Close a room and clean up all peers."""
        if room_id not in self._rooms:
            logger.warning(f"Room {room_id} does not exist")
            return False

        if room_id in self._recordings:
            await self.stop_recording(room_id)

        for peer_id, peer in self._rooms[room_id].items():
            try:
                await peer.pc.close()
            except Exception as e:
                logger.warning(f"Error closing peer {peer_id}: {e}")
            self._last_quality_sample.pop(peer_id, None)

        del self._rooms[room_id]
        logger.info(f"Closed aiortc SFU room: {room_id}")
        return True

    async def join_room(self, room_id: str, peer_id: str) -> Dict[str, Any]:
        """Join a peer to a room."""
        if room_id not in self._rooms:
            await self.create_room(room_id)

        if peer_id in self._rooms[room_id]:
            logger.warning(f"Peer {peer_id} already in room {room_id}")
            return await self.get_router_capabilities(room_id)

        pc = RTCPeerConnection()

        peer = AiortcPeer(peer_id=peer_id, pc=pc)
        self._rooms[room_id][peer_id] = peer

        @pc.on("track")
        async def on_track(track):
            processed_track = track
            if track.kind == "audio":
                if self._acoustic_defense.enabled:
                    # Contain the wrap: a processing failure must not stop the
                    # track from being registered for this peer (the caller
                    # would otherwise fall back to the unwrapped receiver
                    # track and lose the acoustic defense silently).
                    try:
                        processed_track = AudioProcessingTrack(
                            track, self._acoustic_defense
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Acoustic defense wrap failed for peer {peer_id} "
                            f"in room {room_id}, using the raw track: {exc}"
                        )
                        processed_track = track
                # L1 (Agent 34): renegotiation redelivers tracks; appending
                # unconditionally accumulated duplicate/ghost entries. Dedupe
                # and prune to a bounded set per kind.
                if processed_track not in peer.incoming_audio_tracks:
                    peer.incoming_audio_tracks.append(processed_track)
                    self._prune_incoming_tracks(peer.incoming_audio_tracks)
                logger.debug(
                    f"Audio track received from peer {peer_id} in room {room_id}"
                )
            elif track.kind == "video":
                if track not in peer.incoming_video_tracks:
                    peer.incoming_video_tracks.append(track)
                    self._prune_incoming_tracks(peer.incoming_video_tracks)
                logger.debug(
                    f"Video track received from peer {peer_id} in room {room_id}"
                )

            if track.kind == "audio":
                recording = self._recordings.get(room_id)
                if recording is not None and AIORTC_RECORDING_AVAILABLE:
                    rec_producer_id = str(uuid.uuid4())
                    filepath = os.path.join(
                        recording["output_dir"],
                        f"{recording['recording_id']}_{peer_id}_{rec_producer_id}.webm",
                    )
                    try:
                        assert AIORTC_RECORDING_AVAILABLE
                        recorder = MediaRecorder(filepath)
                        recorder.addTrack(processed_track)
                        await recorder.start()
                        recording["recorders"].append(
                            {
                                "producer_id": rec_producer_id,
                                "peer_id": peer_id,
                                "filepath": filepath,
                                "recorder": recorder,
                            }
                        )
                        logger.info(
                            f"Auto-recorded audio track for peer {peer_id} in room {room_id}"
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Failed to auto-record track for peer {peer_id}: {exc}"
                        )

        @pc.on("negotiationneeded")
        async def on_negotiation_needed():
            try:
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)
            except Exception as e:
                logger.warning(f"Negotiation failed for peer {peer_id}: {e}")

        # Forward existing producers to the new peer
        for existing_id, existing_peer in self._rooms[room_id].items():
            if existing_id == peer_id:
                continue
            for producer_id, track in existing_peer.producer_tracks.items():
                if track is None:
                    continue
                try:
                    peer.pc.addTrack(_sender_view(track))
                except Exception as e:
                    logger.debug(
                        f"Could not add track {producer_id} from {existing_id} "
                        f"to new peer {peer_id}: {e}"
                    )

        logger.info(f"Peer {peer_id} joined room {room_id}")
        return await self.get_router_capabilities(room_id)

    # ------------------------------------------------------------------
    # Track / sender hygiene
    # ------------------------------------------------------------------

    @staticmethod
    def _prune_incoming_tracks(tracks: List[Any], max_len: int = 4) -> None:
        """Bound an incoming-track list so renegotiation cannot grow it."""
        if len(tracks) > max_len:
            del tracks[: len(tracks) - max_len]

    @staticmethod
    def _sender_for_track(peer: AiortcPeer, track: Any) -> Any:
        """Return a sender already forwarding ``track``, including suspended ones.

        A sender suspended by a degradation hint reports ``track is None``, so
        identity against ``sender.track`` alone is not enough: re-adding the
        track would create a duplicate sender and trigger a spurious
        renegotiation.
        """
        if track is None:
            return None
        for sender in peer.pc.getSenders():
            if _same_sender_track(getattr(sender, "track", None), track):
                return sender
            if peer.degraded_senders.get(sender) is track:
                return sender
        return None

    def _remove_forwarded_senders(self, room_id: str, track: Any) -> None:
        """Remove every sender forwarding ``track`` on other peers' PCs.

        L1 (Agent 34): forwarded tracks added to other peers were never
        removed when the producer closed or the producing peer left, leaving
        ghost senders on live peer connections.
        """
        if room_id not in self._rooms or track is None:
            return

        for peer in self._rooms[room_id].values():
            try:
                senders = list(peer.pc.getSenders())
            except Exception:
                continue
            for sender in senders:
                if (
                    _same_sender_track(getattr(sender, "track", None), track)
                    or peer.degraded_senders.get(sender) is track
                ):
                    peer.degraded_senders.pop(sender, None)
                    try:
                        peer.pc.removeTrack(sender)
                    except Exception as e:
                        logger.debug(
                            f"Could not remove ghost sender on {peer.peer_id}: {e}"
                        )

    async def apply_remote_offer(
        self,
        room_id: str,
        peer_id: str,
        offer_sdp: str,
    ) -> Dict[str, Any]:
        """Answer the client's offer on the peer's aiortc connection.

        SECURITY-correctness (audit finding: SFU/media path dead):
        without setRemoteDescription the aiortc peer never learns the
        remote ICE/DTLS parameters, the handshake cannot complete, and
        no media flows despite the signaling layer reporting success.

        The client's offer also makes THIS peer the **answerer**: nothing
        creates a local description until an answer is built and installed.
        Returning early used to leave ``localDescription`` empty, so the
        caller fell back to ``create_transport()``, whose
        ``createOffer()``/``setLocalDescription()`` raised
        ``InvalidStateError`` in the ``have-remote-offer`` state -- the
        client received synthetic SDP and media could never flow on the
        in-process backend. The answer is rebuilt on every offer so
        renegotiation works too.
        """
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            raise SFUConnectionError(f"Peer {peer_id} not in room {room_id}")
        try:
            from aiortc import RTCSessionDescription

            remote = RTCSessionDescription(sdp=offer_sdp, type="offer")
        except Exception as e:
            raise SFUConnectionError(f"Invalid offer SDP: {e}") from e
        try:
            await peer.pc.setRemoteDescription(remote)
        except Exception as e:
            raise SFUConnectionError(f"Failed to apply remote offer: {e}") from e

        if peer.pc.signalingState == "have-remote-offer":
            try:
                answer = await peer.pc.createAnswer()
                await peer.pc.setLocalDescription(answer)
            except Exception as e:
                raise SFUConnectionError(f"Failed to build SFU answer: {e}") from e

        local = peer.pc.localDescription
        if local is not None and local.sdp:
            return {"sdp": local.sdp, "type": local.type}
        return {}

    async def apply_remote_answer(
        self,
        room_id: str,
        peer_id: str,
        answer_sdp: str,
    ) -> bool:
        """Apply the client's answer so the aiortc DTLS handshake can run."""
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            return False
        try:
            from aiortc import RTCSessionDescription

            remote = RTCSessionDescription(sdp=answer_sdp, type="answer")
        except Exception as e:
            logger.warning(f"Invalid answer SDP for {peer_id}: {e}")
            return False
        try:
            await peer.pc.setRemoteDescription(remote)
            return True
        except Exception as e:
            logger.warning(f"Failed to apply remote answer for {peer_id}: {e}")
            return False

    async def add_ice_candidate(
        self,
        room_id: str,
        peer_id: str,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """Feed a trickled client candidate into the peer connection.

        The signaling layer records candidates but, without this, they never
        reach the SFU -- on loopback ICE still completes via peer-reflexive
        discovery, which is why the gap stayed hidden; across a NAT the SFU has
        no way to initiate a check toward the client. aiortc requires a
        candidate to be bound to an m-line (``sdpMid`` or ``sdpMLineIndex``),
        so candidates arriving without one are refused rather than applied to
        the wrong transceiver.
        """
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            return False
        if sdp_mid is None and sdp_mline_index is None:
            logger.debug(
                f"Dropping ICE candidate for peer {peer_id}: no sdpMid/mLineIndex"
            )
            return False
        try:
            from ..ice import ICECandidateParser
            from aiortc import RTCIceCandidate

            parsed = ICECandidateParser().parse(candidate)
            remote = RTCIceCandidate(
                component=int(parsed["component"]),
                foundation=str(parsed["foundation"]),
                ip=str(parsed["address"]),
                port=int(parsed["port"]),
                priority=int(parsed["priority"]),
                protocol=str(parsed["transport"]).lower(),
                type=str(parsed["type"]),
                relatedAddress=parsed.get("related_address"),
                relatedPort=parsed.get("related_port"),
                sdpMid=sdp_mid,
                sdpMLineIndex=sdp_mline_index,
                tcpType=parsed.get("tcp_type"),
            )
            await peer.pc.addIceCandidate(remote)
            return True
        except Exception as e:
            # A malformed or out-of-order candidate must not break the session.
            logger.debug(f"Failed to add ICE candidate for peer {peer_id}: {e}")
            return False

    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room."""
        if room_id not in self._rooms:
            return False

        if peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        # L1 (Agent 34): before tearing down this peer, remove the tracks it
        # produced from every other peer's connection so ghost senders do not
        # outlive the producing peer.
        produced_tracks = [
            track
            for track in list(peer.producer_tracks.values())
            + list(peer.incoming_audio_tracks)
            + list(peer.incoming_video_tracks)
        ]
        for track in produced_tracks:
            self._remove_forwarded_senders(room_id, track)

        try:
            await peer.pc.close()
        except Exception as e:
            logger.warning(f"Error closing peer connection: {e}")

        peer.incoming_audio_tracks.clear()
        peer.incoming_video_tracks.clear()
        peer.producer_tracks.clear()
        peer.producers.clear()
        peer.consumers.clear()
        peer.degraded_senders.clear()

        del self._rooms[room_id][peer_id]
        self._last_quality_sample.pop(peer_id, None)
        logger.info(f"Peer {peer_id} left room {room_id}")

        if not self._rooms[room_id]:
            await self.close_room(room_id)

        return True

    # ------------------------------------------------------------------
    # Transport management
    # ------------------------------------------------------------------

    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """Create a WebRTC transport for a peer with real ICE/DTLS parameters."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError(f"Peer {peer_id} not in room {room_id}")

        peer = self._rooms[room_id][peer_id]
        transport_id = str(uuid.uuid4())
        pc = peer.pc

        if pc.localDescription is None:
            # The offer dance is only legal from "stable". In a pending remote
            # offer (the client-offer flow) this peer is the answerer and
            # ``apply_remote_offer`` must have already installed the answer --
            # raise a diagnosable error instead of letting aiortc surface an
            # InvalidStateError from deep inside setLocalDescription.
            if pc.signalingState != "stable":
                raise SFUConnectionError(
                    f"Cannot create a local offer for peer {peer_id} in "
                    f"signaling state '{pc.signalingState}'"
                )
            pc.createDataChannel("transport-init-" + transport_id)
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            if pc.iceGatheringState != "complete":
                done = asyncio.Event()

                @pc.on("icegatheringstatechange")
                def on_ice_state():
                    if pc.iceGatheringState == "complete":
                        done.set()

                try:
                    await asyncio.wait_for(done.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.debug(f"ICE gathering timed out for peer {peer_id}")

        if pc.localDescription is None:
            raise SFUConnectionError("Failed to get local description")
        sdp = pc.localDescription.sdp

        transport = SFUTransport(
            id=transport_id,
            direction=direction,
            ice_parameters=_parse_ice_params_from_sdp(sdp),
            ice_candidates=_parse_ice_candidates_from_sdp(sdp),
            dtls_parameters=_parse_dtls_params_from_sdp(sdp),
        )

        peer.transports[transport_id] = transport
        logger.debug(f"Created transport {transport_id} for peer {peer_id}")
        return transport

    async def connect_transport(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        dtls_parameters: Dict[str, Any],
    ) -> bool:
        """Connect a transport with remote DTLS parameters."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        if transport_id not in peer.transports:
            return False

        peer.transports[transport_id].dtls_parameters = dtls_parameters
        logger.debug(f"Connected transport {transport_id} for peer {peer_id}")
        return True

    # ------------------------------------------------------------------
    # Media produce / consume
    # ------------------------------------------------------------------

    def _find_track_by_kind(self, peer: AiortcPeer, kind: MediaKind) -> Any:
        """Locate a MediaStreamTrack of the requested kind on a peer's PC."""
        if kind == MediaKind.AUDIO and peer.incoming_audio_tracks:
            return peer.incoming_audio_tracks[-1]
        if kind == MediaKind.VIDEO and peer.incoming_video_tracks:
            return peer.incoming_video_tracks[-1]

        for receiver in peer.pc.getReceivers():
            if receiver.track and receiver.track.kind == kind.value:
                return receiver.track

        return None

    async def _forward_track_to_peer(
        self,
        track: Any,
        producer_id: str,
        kind: MediaKind,
        target_peer: AiortcPeer,
    ) -> None:
        """Add a track to a target peer's connection and trigger renegotiation."""
        if track is None:
            return

        if self._sender_for_track(target_peer, track) is not None:
            return

        try:
            target_peer.pc.addTrack(_sender_view(track))
            if target_peer.pc.remoteDescription is not None:
                offer = await target_peer.pc.createOffer()
                await target_peer.pc.setLocalDescription(offer)
        except Exception as e:
            logger.debug(
                f"Could not forward track {producer_id} to peer "
                f"{target_peer.peer_id}: {e}"
            )

    async def produce(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        kind: MediaKind,
        rtp_parameters: Dict[str, Any],
    ) -> SFUProducer:
        """Create a producer to send media and forward the track to all other peers."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError(f"Peer {peer_id} not in room {room_id}")

        peer = self._rooms[room_id][peer_id]
        producer_id = str(uuid.uuid4())

        track = self._find_track_by_kind(peer, kind)

        # Wrap audio tracks with acoustic defense processing
        if (
            kind == MediaKind.AUDIO
            and track is not None
            and self._acoustic_defense.enabled
        ):
            track = AudioProcessingTrack(track, self._acoustic_defense)

        producer = SFUProducer(
            id=producer_id,
            kind=kind,
            rtp_parameters=rtp_parameters,
            paused=False,
        )

        peer.producers[producer_id] = producer
        if track:
            peer.producer_tracks[producer_id] = track

        for other_id, other_peer in self._rooms[room_id].items():
            if other_id == peer_id:
                continue
            await self._forward_track_to_peer(track, producer_id, kind, other_peer)

        logger.info(
            f"Created producer {producer_id} ({kind.value}) for peer {peer_id}"
            f" and forwarded to {len(self._rooms[room_id]) - 1} peer(s)"
        )

        return producer

    async def consume(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: Dict[str, Any],
    ) -> SFUConsumer:
        """Create a consumer to receive media from a producer."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError(f"Peer {peer_id} not in room {room_id}")

        peer = self._rooms[room_id][peer_id]
        consumer_id = str(uuid.uuid4())

        producer = None
        producer_track = None
        producer_kind = MediaKind.AUDIO
        for p in self._rooms[room_id].values():
            if producer_id in p.producers:
                producer = p.producers[producer_id]
                producer_track = p.producer_tracks.get(producer_id)
                producer_kind = producer.kind
                break

        if not producer:
            raise SFUConnectionError(f"Producer {producer_id} not found")

        if producer_track is not None:
            already_wired = self._sender_for_track(peer, producer_track) is not None
            if not already_wired:
                try:
                    peer.pc.addTrack(_sender_view(producer_track))
                    if peer.pc.remoteDescription is not None:
                        offer = await peer.pc.createOffer()
                        await peer.pc.setLocalDescription(offer)
                except Exception as e:
                    logger.debug(
                        f"Could not wire consumer {consumer_id} for producer "
                        f"{producer_id} on peer {peer_id}: {e}"
                    )

        consumer = SFUConsumer(
            id=consumer_id,
            producer_id=producer_id,
            kind=producer_kind,
            rtp_parameters=rtp_capabilities,
            paused=False,
        )

        peer.consumers[consumer_id] = consumer
        logger.info(
            f"Created consumer {consumer_id} ({producer_kind.value}) "
            f"for peer {peer_id} consuming producer {producer_id}"
        )

        return consumer

    # ------------------------------------------------------------------
    # Producer pause / resume / close
    # ------------------------------------------------------------------

    async def pause_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Pause a producer."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        if producer_id not in peer.producers:
            return False

        peer.producers[producer_id].paused = True
        logger.debug(f"Paused producer {producer_id}")
        return True

    async def resume_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Resume a producer."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        if producer_id not in peer.producers:
            return False

        peer.producers[producer_id].paused = False
        logger.debug(f"Resumed producer {producer_id}")
        return True

    async def close_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Close a producer."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        if producer_id not in peer.producers:
            return False

        del peer.producers[producer_id]
        track = peer.producer_tracks.pop(producer_id, None)
        # L1 (Agent 34): closing a producer must also stop forwarding its
        # track to every other peer (ghost senders otherwise persist).
        if track is not None:
            self._remove_forwarded_senders(room_id, track)
        logger.debug(f"Closed producer {producer_id}")
        return True

    # ------------------------------------------------------------------
    # Room info & capabilities
    # ------------------------------------------------------------------

    async def get_room_info(self, room_id: str) -> Optional[RoomInfo]:
        """Get information about a room."""
        if room_id not in self._rooms:
            return None

        peers = list(self._rooms[room_id].keys())
        producers = []

        for peer in self._rooms[room_id].values():
            producers.extend(peer.producers.keys())

        return RoomInfo(id=room_id, peers=peers, producers=producers)

    async def get_router_capabilities(self, room_id: str) -> Dict[str, Any]:
        """Get RTP capabilities for a room's router."""
        return {
            "codecs": [
                {
                    "kind": "audio",
                    "mimeType": "audio/opus",
                    "clockRate": 48000,
                    "channels": 2,
                },
                {
                    "kind": "video",
                    "mimeType": "video/VP8",
                    "clockRate": 90000,
                },
                {
                    "kind": "video",
                    "mimeType": "video/VP9",
                    "clockRate": 90000,
                },
                {
                    "kind": "video",
                    "mimeType": "video/H264",
                    "clockRate": 90000,
                },
            ],
            "headerExtensions": [],
            "fecMechanisms": [],
        }

    @staticmethod
    def _bound_video_senders(peer: AiortcPeer) -> bool:
        """Cap the frame rate of every outbound video track for ``peer``.

        The nearest lever to a lower simulcast layer on a backend that
        negotiates a single encoding and has no bitrate API: the peer keeps
        its video -- bounded, instead of losing it entirely or being handed a
        frozen picture. Audio is never touched. Returns whether any sender
        changed state.
        """
        changed = False
        try:
            senders = list(peer.pc.getSenders())
        except Exception as exc:
            logger.debug(f"Could not enumerate senders for {peer.peer_id}: {exc}")
            return False
        for sender in senders:
            track = getattr(sender, "track", None)
            if track is None or getattr(track, "kind", None) != "video":
                continue
            if sender in peer.degraded_senders or isinstance(track, BoundedVideoTrack):
                continue
            try:
                sender.replaceTrack(BoundedVideoTrack(track))
            except Exception as exc:
                logger.debug(f"Could not bound video sender on {peer.peer_id}: {exc}")
                continue
            peer.degraded_senders[sender] = track
            changed = True
        return changed

    @staticmethod
    def _restore_video_senders(peer: AiortcPeer) -> bool:
        """Re-attach full-rate video for every sender a hint degraded."""
        changed = False
        for sender, track in list(peer.degraded_senders.items()):
            try:
                sender.replaceTrack(track)
            except Exception as exc:
                logger.debug(f"Could not restore video sender on {peer.peer_id}: {exc}")
                continue
            peer.degraded_senders.pop(sender, None)
            changed = True
        return changed

    async def set_preferred_layers(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
        spatial_layer: int,
        temporal_layer: int,
    ) -> bool:
        """Apply a degradation hint as far as aiortc allows, or report False.

        aiortc exposes no simulcast API and no bitrate control -- a peer
        connection negotiates a single video encoding and ``RTCRtpSender``
        has no ``setParameters`` -- so the hint is applied as a *bounded*
        frame rate rather than an all-or-nothing stop: a degraded peer keeps
        its video at a low rate (the stream the estimator is reacting to)
        while the encoded bitrate falls, and full rate is re-attached when
        the hint is lifted. Audio is never touched. The hint is reported as
        applied only when a sender actually changed state, so a peer with no
        video -- or one already in the requested state -- still gets an
        honest ``False`` instead of the silent success that made the old
        implementation a lie.
        """
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            logger.debug(
                f"Preferred layers not applied for consumer {consumer_id}: "
                f"peer {peer_id} is not in room {room_id}"
            )
            return False
        if spatial_layer <= 0:
            applied = self._bound_video_senders(peer)
            action = "bounded"
        else:
            applied = self._restore_video_senders(peer)
            action = "restored"
        if not applied:
            logger.debug(
                f"Preferred layers not applied for consumer {consumer_id}: "
                f"no video sender to change (spatial={spatial_layer}, "
                f"temporal={temporal_layer})"
            )
        else:
            logger.info(
                f"Preferred layers applied for consumer {consumer_id}: "
                f"video {action} (spatial={spatial_layer}, "
                f"temporal={temporal_layer})"
            )
        return applied

    async def health_check(self) -> bool:
        """Check if the SFU server is healthy."""
        return AIORTC_AVAILABLE

    # ------------------------------------------------------------------
    # Quality reporting
    # ------------------------------------------------------------------

    def set_quality_mixin(self, quality_mixin: Any) -> None:
        """Wire the quality sink and start polling aiortc connection stats.

        aiortc never pushes scores, so quality is pulled from
        ``RTCPeerConnection.getStats()``. Without this the default in-process
        backend reported nothing at all and BandwidthEstimator had no input,
        the same gap the janus and mediasoup-REST adapters had.
        """
        super().set_quality_mixin(quality_mixin)
        if self._quality_reporter is None:
            self._quality_reporter = QualityReporter(self)
        self._quality_reporter.start()

    def _bitrate_for(
        self, peer_id: str, bytes_sent: Optional[int], now: float
    ) -> Optional[int]:
        """Egress bitrate in bits/s from the change in the peer's byte counter.

        Returns ``None`` for the first observation of a peer: a rate needs two
        samples, and a fabricated one would drive degradation decisions.
        """
        if bytes_sent is None:
            return None
        previous = self._last_quality_sample.get(peer_id)
        self._last_quality_sample[peer_id] = (bytes_sent, now)
        if previous is None:
            return None
        last_bytes, last_ts = previous
        elapsed = now - last_ts
        delta = bytes_sent - last_bytes
        if elapsed <= 0 or delta < 0:
            return None
        return int(delta * 8.0 / elapsed)

    async def collect_quality(self) -> List[tuple]:
        """Poll each peer's aiortc stats into canonical quality samples.

        Returns ``(room_id, peer_id, metrics)`` with the SFU-scoped ids the
        manager minted, so the base ``report_quality`` can map them back to
        the numeric ``(channel_id, user_id)`` the quality store is keyed by.
        Peers whose pc reports nothing measurable are skipped.
        """
        samples: List[tuple] = []
        now = time.monotonic()
        for room_id, peers in list(self._rooms.items()):
            for peer_id, peer in list(peers.items()):
                try:
                    records = await peer.pc.getStats()
                except Exception as exc:
                    logger.debug(
                        f"aiortc quality poll failed for peer {peer_id} "
                        f"in room {room_id}: {exc}"
                    )
                    continue
                metrics = _quality_metrics_from_stats(records)
                bitrate = self._bitrate_for(peer_id, _total_bytes_sent(records), now)
                if bitrate is not None:
                    metrics["bitrate"] = bitrate
                if metrics:
                    samples.append((room_id, peer_id, metrics))
        return samples

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    async def start_recording(self, room_id: str, output_dir: str) -> Dict[str, Any]:
        """Start recording audio in a room.

        Creates a ``MediaRecorder`` per audio track currently in the room.
        New tracks that arrive after this call are auto-recorded via the
        ``on_track`` handler added in :meth:`join_room`.
        """
        if room_id not in self._rooms:
            raise SFUConnectionError(f"Room {room_id} does not exist")

        if room_id in self._recordings:
            logger.warning(f"Room {room_id} is already recording")
            return {"recording_id": self._recordings[room_id]["recording_id"]}

        if not AIORTC_RECORDING_AVAILABLE:
            logger.warning(
                f"Recording requested for room {room_id} but MediaRecorder is not available"
            )
            return {"recording_id": "", "file_count": 0, "unsupported": True}

        recording_id = str(uuid.uuid4())
        os.makedirs(output_dir, exist_ok=True)

        assert AIORTC_RECORDING_AVAILABLE
        recorders: List[Dict[str, Any]] = []
        for pid, peer in self._rooms[room_id].items():
            for idx, track in enumerate(peer.incoming_audio_tracks):
                rec_producer_id = str(uuid.uuid4())
                filepath = os.path.join(
                    output_dir,
                    f"{recording_id}_{pid}_{rec_producer_id}.webm",
                )
                try:
                    recorder = MediaRecorder(filepath)
                    recorder.addTrack(track)
                    await recorder.start()
                    recorders.append(
                        {
                            "producer_id": rec_producer_id,
                            "peer_id": pid,
                            "filepath": filepath,
                            "recorder": recorder,
                        }
                    )
                    logger.debug(f"Recording track {idx} for peer {pid} -> {filepath}")
                except Exception as exc:
                    logger.warning(f"Failed to start recorder for peer {pid}: {exc}")

        self._recordings[room_id] = {
            "recording_id": recording_id,
            "output_dir": output_dir,
            "recorders": recorders,
        }

        logger.info(
            f"Started recording room {room_id}: "
            f"{len(recorders)} track(s), recording_id={recording_id}"
        )
        return {
            "recording_id": recording_id,
            "file_count": len(recorders),
        }

    async def stop_recording(self, room_id: str) -> Optional[List[str]]:
        """Stop recording a room and return the recorded file paths."""
        if room_id not in self._recordings:
            logger.info(f"Room {room_id} is not recording")
            return None

        info = self._recordings.pop(room_id)
        filepaths: List[str] = []
        for rec_info in info["recorders"]:
            try:
                await rec_info["recorder"].stop()
                filepaths.append(rec_info["filepath"])
                logger.debug(
                    f"Stopped recorder for peer {rec_info['peer_id']}: "
                    f"{rec_info['filepath']}"
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to stop recorder for producer "
                    f"{rec_info.get('producer_id', '?')}: {exc}"
                )

        logger.info(f"Stopped recording room {room_id}: {len(filepaths)} file(s)")
        return filepaths if filepaths else None

    async def close(self) -> None:
        """Close the adapter and clean up all resources."""
        if self._quality_reporter is not None:
            self._quality_reporter.stop()
            self._quality_reporter = None
        self._last_quality_sample.clear()

        for room_id in list(self._recordings.keys()):
            await self.stop_recording(room_id)

        for room_id in list(self._rooms.keys()):
            await self.close_room(room_id)

        logger.info("aiortc SFU adapter closed")
