"""
aiortc SFU adapter - Pure Python WebRTC SFU implementation.

This adapter uses aiortc to implement an SFU directly in Python,
eliminating the need for external SFU services like mediasoup or Janus.
It runs in-process with the FastAPI application for maximum integration.
"""

import asyncio
import os
import re
import uuid
from typing import Dict, List, Optional, Any
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


@dataclass
class AiortcPeer:
    """A peer in the aiortc SFU."""

    peer_id: str
    pc: RTCPeerConnection  # type: ignore
    producers: Dict[str, SFUProducer] = field(default_factory=dict)
    consumers: Dict[str, SFUConsumer] = field(default_factory=dict)
    transports: Dict[str, SFUTransport] = field(default_factory=dict)
    incoming_audio_tracks: List[Any] = field(default_factory=list)
    incoming_video_tracks: List[Any] = field(default_factory=list)
    producer_tracks: Dict[str, Any] = field(default_factory=dict)


class AiortcAdapter(SFUAdapter):
    """
    aiortc-based SFU adapter.

    This adapter implements an SFU using aiortc directly in Python.
    It manages peer connections and routes media streams between peers.
    """

    def __init__(self, ice_servers: Optional[List[Dict[str, Any]]] = None):
        """
        Initialize the aiortc SFU adapter.

        Args:
            ice_servers: List of STUN/TURN server configurations in aiortc format
                [{"urls": "stun:..."}, {"urls": "turn:...", "username": "...", "credential": "..."}]
        """
        if not AIORTC_AVAILABLE:
            raise SFUConnectionError(
                "aiortc is not installed. Install with: pip install aiortc==1.14.0"
            )

        self._rooms: Dict[str, Dict[str, AiortcPeer]] = {}
        self._ice_servers = ice_servers or []
        self._recordings: Dict[str, Dict[str, Any]] = {}
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
            if track.kind == "audio":
                peer.incoming_audio_tracks.append(track)
                logger.debug(
                    f"Audio track received from peer {peer_id} in room {room_id}"
                )
            elif track.kind == "video":
                peer.incoming_video_tracks.append(track)
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
                        recorder.addTrack(track)
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
                    peer.pc.addTrack(track)
                except Exception as e:
                    logger.debug(
                        f"Could not add track {producer_id} from {existing_id} "
                        f"to new peer {peer_id}: {e}"
                    )

        logger.info(f"Peer {peer_id} joined room {room_id}")
        return await self.get_router_capabilities(room_id)

    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room."""
        if room_id not in self._rooms:
            return False

        if peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        try:
            await peer.pc.close()
        except Exception as e:
            logger.warning(f"Error closing peer connection: {e}")

        del self._rooms[room_id][peer_id]
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

        try:
            target_peer.pc.addTrack(track)
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
            already_wired = False
            for sender in peer.pc.getSenders():
                if sender.track is producer_track:
                    already_wired = True
                    break
            if not already_wired:
                try:
                    peer.pc.addTrack(producer_track)
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
        peer.producer_tracks.pop(producer_id, None)
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

    async def set_preferred_layers(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
        spatial_layer: int,
        temporal_layer: int,
    ) -> bool:
        """Set preferred simulcast layers for a consumer.

        aiortc does not expose the same simulcast API as mediasoup.
        This is a no-op that logs the request.
        """
        logger.debug(
            f"Set preferred layers for consumer {consumer_id}: "
            f"spatial={spatial_layer}, temporal={temporal_layer} "
            f"(not implemented in aiortc adapter)"
        )
        return True

    async def health_check(self) -> bool:
        """Check if the SFU server is healthy."""
        return AIORTC_AVAILABLE

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
        for room_id in list(self._recordings.keys()):
            await self.stop_recording(room_id)

        for room_id in list(self._rooms.keys()):
            await self.close_room(room_id)

        logger.info("aiortc SFU adapter closed")
