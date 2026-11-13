"""aiortc SFU adapter implementation (hardened)."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import time as _time_mod
import uuid
from typing import Any, Dict, List, Optional, Tuple, cast

import utils.logger as logger

from ...exceptions import SFUConnectionError
from ..acoustic_defense import AudioProcessingTrack
from ..base import (
    AcousticDefenseConfig,
    MediaKind,
    RoomInfo,
    SFUAdapter,
    SFUConsumer,
    SFUProducer,
    SFUTransport,
    TransportDirection,
)
from ..fanout import FanoutView, TrackFanout
from ..quality import QualityReporter
from .constants import (
    DEFAULT_MAX_INCOMING_TRACKS,
    MAX_CONSUMERS_PER_PEER,
    MAX_PEERS_PER_ROOM,
    MAX_PRODUCERS_PER_PEER,
    MAX_TRACKS_PER_KIND,
    MAX_TRANSPORTS_PER_PEER,
)
from .errors import SFUValidationError
from .media import BoundedVideoTrack, _same_sender_track
from .models import AiortcPeer
from .sdp import (
    _parse_dtls_params_from_sdp,
    _parse_ice_candidates_from_sdp,
    _parse_ice_params_from_sdp,
)
from .stats import _as_number, _quality_metrics_from_stats
from .validation import (
    validate_dtls_parameters,
    validate_ice_candidate_payload,
    validate_identifier,
    validate_payload_mapping,
    validate_recording_output_dir,
    validate_room_id,
    validate_sdp_payload,
)

import sys as _sys

_pkg = _sys.modules[__name__.rsplit(".", 1)[0]]
# Patchable time (tests monkeypatch aiortc_adapter.time.monotonic).
time = getattr(_pkg, "time", _time_mod)


def _rtc_peer_connection_cls() -> Any:
    return getattr(_pkg, "RTCPeerConnection", None)


def _media_recorder_cls() -> Any:
    return getattr(_pkg, "MediaRecorder", None)


def _aiortc_available() -> bool:
    return bool(getattr(_pkg, "AIORTC_AVAILABLE", False))


def _aiortc_recording_available() -> bool:
    return bool(getattr(_pkg, "AIORTC_RECORDING_AVAILABLE", False))


def _audio_processing_track_cls() -> Any:
    return getattr(_pkg, "AudioProcessingTrack", AudioProcessingTrack)


def _rtc_session_description(**kwargs: Any) -> Any:
    mod = cast(Any, importlib.import_module("aiortc"))
    return mod.RTCSessionDescription(**kwargs)


def _rtc_ice_candidate(**kwargs: Any) -> Any:
    mod = cast(Any, importlib.import_module("aiortc"))
    return mod.RTCIceCandidate(**kwargs)


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


def _make_rtc_peer_connection() -> Any:
    cls = _rtc_peer_connection_cls()
    if cls is None:
        raise SFUConnectionError("aiortc is not installed")
    return cls()


def _make_media_recorder(filepath: str) -> Any:
    cls = _media_recorder_cls()
    if cls is None:
        raise SFUConnectionError("MediaRecorder is not available")
    return cls(filepath)


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
        if not _aiortc_available():
            raise SFUConnectionError(
                "aiortc is not installed. Install with: pip install aiortc==1.14.0"
            )

        self._rooms: Dict[str, Dict[str, AiortcPeer]] = {}
        self._ice_servers = ice_servers or []
        self._acoustic_defense: Any = acoustic_defense or AcousticDefenseConfig()
        # Deliverer of an SFU-initiated offer (see set_renegotiation_sink).
        self._renegotiation_sink: Any = None
        # One read-once relay per produced track that is forwarded to several
        # peers, so every sender receives every frame instead of a fraction.
        self._fanouts: Dict[Any, TrackFanout] = {}
        self._recordings: Dict[str, Dict[str, Any]] = {}
        # Quality bridge state (see ``collect_quality``).
        self._quality_mixin: Any = None
        self._quality_reporter: Optional[QualityReporter] = None
        # Last ``(bytesSent, monotonic ts)`` per peer, for bitrate deltas.
        self._last_quality_sample: Dict[str, Tuple[int, float]] = {}
        if _aiortc_recording_available():
            logger.info("aiortc MediaRecorder available for call recording")
        else:
            logger.info("aiortc MediaRecorder not available - call recording disabled")
        logger.info("aiortc SFU adapter initialized")

    # ------------------------------------------------------------------
    # Room lifecycle
    # ------------------------------------------------------------------

    async def create_room(self, room_id: str) -> RoomInfo:
        """Create a new room."""
        room_id = validate_room_id(room_id)
        if room_id in self._rooms:
            logger.warning("SFU room already exists; returning existing room")
            room_info = await self.get_room_info(room_id)
            if room_info is None:
                raise SFUConnectionError("Failed to get room info")
            return room_info

        self._rooms[room_id] = {}
        logger.info("Created aiortc SFU room")
        return RoomInfo(id=room_id, peers=[], producers=[])

    async def close_room(self, room_id: str) -> bool:
        """Close a room and clean up all peers."""
        try:
            room_id = validate_room_id(room_id)
        except SFUValidationError:
            return False
        if room_id not in self._rooms:
            return False

        if room_id in self._recordings:
            await self.stop_recording(room_id)

        for peer_id, peer in self._rooms[room_id].items():
            for track in list(peer.producer_tracks.values()):
                self._close_fanout(track)
            try:
                await peer.pc.close()
            except Exception:
                logger.warning("Error closing peer connection")
            self._last_quality_sample.pop(peer_id, None)

        del self._rooms[room_id]
        logger.info("Closed aiortc SFU room")
        return True

    async def join_room(self, room_id: str, peer_id: str) -> Dict[str, Any]:
        """Join a peer to a room."""
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        if room_id not in self._rooms:
            await self.create_room(room_id)

        if peer_id in self._rooms[room_id]:
            logger.warning("Peer already in room")
            return await self.get_router_capabilities(room_id)

        if len(self._rooms[room_id]) >= MAX_PEERS_PER_ROOM:
            raise SFUValidationError("Room peer limit exceeded")
        pc = _make_rtc_peer_connection()
        peer = AiortcPeer(peer_id=peer_id, pc=pc, room_id=room_id)
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
                if (
                    len(peer.incoming_audio_tracks) < MAX_TRACKS_PER_KIND
                    and processed_track not in peer.incoming_audio_tracks
                ):
                    peer.incoming_audio_tracks.append(processed_track)
                    self._prune_incoming_tracks(
                        peer.incoming_audio_tracks, DEFAULT_MAX_INCOMING_TRACKS
                    )
                logger.debug("Audio track received")
            elif track.kind == "video":
                if (
                    len(peer.incoming_video_tracks) < MAX_TRACKS_PER_KIND
                    and track not in peer.incoming_video_tracks
                ):
                    peer.incoming_video_tracks.append(track)
                    self._prune_incoming_tracks(
                        peer.incoming_video_tracks, DEFAULT_MAX_INCOMING_TRACKS
                    )
                logger.debug("Video track received")

            if track.kind == "audio":
                recording = self._recordings.get(room_id)
                if recording is not None and _aiortc_recording_available():
                    rec_producer_id = str(uuid.uuid4())
                    filepath = os.path.join(
                        recording["output_dir"],
                        f"{recording['recording_id']}_{peer_id}_{rec_producer_id}.webm",
                    )
                    try:
                        assert _aiortc_recording_available()
                        recorder = _make_media_recorder(filepath)
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
                    peer.pc.addTrack(self._sender_view(track))
                except Exception as e:
                    logger.debug(
                        f"Could not add track {producer_id} from {existing_id} "
                        f"to new peer {peer_id}: {e}"
                    )

        logger.info("Peer joined room")
        return await self.get_router_capabilities(room_id)

    # ------------------------------------------------------------------
    # Track / sender hygiene
    # ------------------------------------------------------------------

    @staticmethod
    def _prune_incoming_tracks(tracks: List[Any], max_len: int = 4) -> None:
        """Bound an incoming-track list so renegotiation cannot grow it."""
        if len(tracks) > max_len:
            del tracks[: len(tracks) - max_len]

    def _fanout_for(self, track: Any) -> TrackFanout:
        """The shared read-once relay for ``track``, created on first use."""
        relay = self._fanouts.get(track)
        if relay is None or relay.closed:
            relay = TrackFanout(track)
            self._fanouts[track] = relay
        return relay

    def _sender_view(self, track: Any) -> FanoutView:
        """A per-sender view of ``track`` that receives every frame.

        Handing the same track to several senders made each ``recv()`` *take* a
        frame from a shared queue, so peers split the stream; and aiortc stops a
        sender's track when its RTP task exits, so one peer leaving ended the
        room's media. A view reads from the relay's private queue and its
        ``stop()`` detaches that sender alone.
        """
        return self._fanout_for(track).subscribe()

    async def _request_renegotiation(
        self, room_id: str, peer_id: str, peer: AiortcPeer
    ) -> bool:
        """Offer to a peer that has already negotiated, and deliver it.

        aiortc never emits ``negotiationneeded``, so the SFU can never start an
        offer by itself: a track forwarded to a peer that has already finished
        its handshake would be added to a transceiver the client never learns
        about. The offer is built here and handed to the renegotiation sink;
        without a sink there is no signaling path, so this reports ``False``
        rather than pretending the track was delivered.
        """
        if peer.pc is None or peer.pc.remoteDescription is None:
            # Nothing to renegotiate with yet: the peer's first offer is still
            # to come, and the track will be bound when it arrives.
            return False
        try:
            offer = await peer.pc.createOffer()
            await peer.pc.setLocalDescription(offer)
        except Exception as exc:
            logger.debug(f"Renegotiation offer failed for peer {peer_id}: {exc}")
            return False
        local = peer.pc.localDescription
        if local is None or not local.sdp:
            return False

        sink = self._renegotiation_sink
        if sink is None:
            logger.debug(
                f"Offer for peer {peer_id} in room {room_id} has no "
                "renegotiation sink and no signaling path to the client"
            )
            return False
        try:
            result = sink(room_id, peer_id, local.sdp)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning(
                f"Failed to deliver renegotiation offer for {peer_id}: {exc}"
            )
            return False
        return True

    def _close_fanout(self, track: Any) -> None:
        """Drop ``track``'s relay once nothing forwards it any more."""
        relay = self._fanouts.pop(track, None)
        if relay is None:
            return
        task = getattr(relay, "_task", None)
        if task is not None and not task.done():
            task.cancel()

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
            except Exception as sender_error:
                logger.debug(f"peer sender enumeration failed: {sender_error}")
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
        """Answer the client's offer on the peer's aiortc connection."""
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        offer_sdp = validate_sdp_payload(offer_sdp, "offer SDP")
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            raise SFUConnectionError("Peer not in room")
        try:
            remote = _rtc_session_description(sdp=offer_sdp, type="offer")
            await peer.pc.setRemoteDescription(remote)
        except SFUConnectionError:
            raise
        except Exception as exc:
            raise SFUConnectionError("Failed to apply remote offer") from exc

        if peer.pc.signalingState == "have-remote-offer":
            try:
                answer = await peer.pc.createAnswer()
                await peer.pc.setLocalDescription(answer)
            except Exception as exc:
                raise SFUConnectionError("Failed to build SFU answer") from exc

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
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            answer_sdp = validate_sdp_payload(answer_sdp, "answer SDP")
        except SFUValidationError:
            return False
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            return False
        try:
            remote = _rtc_session_description(sdp=answer_sdp, type="answer")
            await peer.pc.setRemoteDescription(remote)
            return True
        except Exception:
            logger.warning("Failed to apply remote answer")
            return False

    async def add_ice_candidate(
        self,
        room_id: str,
        peer_id: str,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """Feed a trickled client candidate into the peer connection."""
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            cand, mid, idx = validate_ice_candidate_payload(
                candidate, sdp_mid, sdp_mline_index
            )
        except SFUValidationError:
            return False
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            return False
        try:
            ice_mod = cast(Any, importlib.import_module("...ice", __package__))
            parsed = ice_mod.ICECandidateParser().parse(cand)
            remote = _rtc_ice_candidate(
                component=int(parsed["component"]),
                foundation=str(parsed["foundation"]),
                ip=str(parsed["address"]),
                port=int(parsed["port"]),
                priority=int(parsed["priority"]),
                protocol=str(parsed["transport"]).lower(),
                type=str(parsed["type"]),
                relatedAddress=parsed.get("related_address"),
                relatedPort=parsed.get("related_port"),
                sdpMid=mid,
                sdpMLineIndex=idx,
                tcpType=parsed.get("tcp_type"),
            )
            await peer.pc.addIceCandidate(remote)
            return True
        except Exception:
            logger.debug("Failed to add ICE candidate")
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
        for track in list(peer.producer_tracks.values()):
            self._close_fanout(track)

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
        logger.info("Peer left room")

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
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        if not isinstance(direction, TransportDirection):
            raise SFUValidationError("Invalid transport direction")
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError("Peer not in room")

        peer = self._rooms[room_id][peer_id]
        if len(peer.transports) >= MAX_TRANSPORTS_PER_PEER:
            raise SFUValidationError("Transport limit exceeded")
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
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            transport_id = validate_identifier(transport_id, "transport_id")
            dtls = validate_dtls_parameters(dtls_parameters)
        except SFUValidationError:
            return False
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False
        peer = self._rooms[room_id][peer_id]
        if transport_id not in peer.transports:
            return False
        peer.transports[transport_id].dtls_parameters = dtls
        logger.debug("Connected transport")
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
            target_peer.pc.addTrack(self._sender_view(track))
            await self._request_renegotiation(
                target_peer.room_id, target_peer.peer_id, target_peer
            )
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
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        transport_id = validate_identifier(transport_id, "transport_id")
        rtp_parameters = validate_payload_mapping(rtp_parameters, "rtp_parameters")
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError("Peer not in room")

        peer = self._rooms[room_id][peer_id]
        if len(peer.producers) >= MAX_PRODUCERS_PER_PEER:
            raise SFUValidationError("Producer limit exceeded")
        producer_id = str(uuid.uuid4())

        track = self._find_track_by_kind(peer, kind)

        # Wrap audio tracks with acoustic defense processing. The receiver
        # track was already wrapped on arrival (``on_track``), so only wrap a
        # track that is not -- otherwise every frame is processed twice.
        if (
            kind == MediaKind.AUDIO
            and track is not None
            and self._acoustic_defense.enabled
            and not isinstance(track, _audio_processing_track_cls())
        ):
            track = _audio_processing_track_cls()(track, self._acoustic_defense)

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
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        transport_id = validate_identifier(transport_id, "transport_id")
        producer_id = validate_identifier(producer_id, "producer_id")
        rtp_capabilities = validate_payload_mapping(
            rtp_capabilities, "rtp_capabilities"
        )
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError("Peer not in room")

        peer = self._rooms[room_id][peer_id]
        if len(peer.consumers) >= MAX_CONSUMERS_PER_PEER:
            raise SFUValidationError("Consumer limit exceeded")
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
                    peer.pc.addTrack(self._sender_view(producer_track))
                    await self._request_renegotiation(room_id, peer_id, peer)
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
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            producer_id = validate_identifier(producer_id, "producer_id")
        except SFUValidationError:
            return False
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
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            producer_id = validate_identifier(producer_id, "producer_id")
        except SFUValidationError:
            return False
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
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            producer_id = validate_identifier(producer_id, "producer_id")
        except SFUValidationError:
            return False
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
            self._close_fanout(track)
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
        return _aiortc_available()

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
        now = getattr(_pkg, "time", _time_mod).monotonic()
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
        """Start recording audio in a room."""
        room_id = validate_room_id(room_id)
        if room_id not in self._rooms:
            raise SFUConnectionError(f"Room {room_id} does not exist")
        if room_id in self._recordings:
            logger.warning("Room is already recording")
            return {"recording_id": self._recordings[room_id]["recording_id"]}
        if not _aiortc_recording_available():
            logger.warning("Recording requested but MediaRecorder is not available")
            return {"recording_id": "", "file_count": 0, "unsupported": True}
        safe_dir = validate_recording_output_dir(output_dir)
        recording_id = str(uuid.uuid4())
        os.makedirs(safe_dir, exist_ok=True)
        recorders: List[Dict[str, Any]] = []
        for pid, peer in self._rooms[room_id].items():
            for _idx, track in enumerate(peer.incoming_audio_tracks):
                rec_producer_id = str(uuid.uuid4())
                filename = f"{recording_id}_{pid}_{rec_producer_id}.webm"
                filepath = os.path.normpath(os.path.join(safe_dir, filename))
                if not (filepath == safe_dir or filepath.startswith(safe_dir + os.sep)):
                    continue
                try:
                    recorder = _make_media_recorder(filepath)
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
                    logger.debug("Recording track started")
                except Exception as exc:
                    logger.warning("Failed to start recorder: %s", type(exc).__name__)
        self._recordings[room_id] = {
            "recording_id": recording_id,
            "output_dir": safe_dir,
            "recorders": recorders,
        }
        logger.info("Started recording: %d track(s)", len(recorders))
        return {"recording_id": recording_id, "file_count": len(recorders)}

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

        for relay in list(self._fanouts.values()):
            await relay.aclose()
        self._fanouts.clear()

        logger.info("aiortc SFU adapter closed")
