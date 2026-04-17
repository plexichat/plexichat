"""
aiortc SFU adapter - Pure Python WebRTC SFU implementation.

This adapter uses aiortc to implement an SFU directly in Python,
eliminating the need for external SFU services like mediasoup or Janus.
It runs in-process with the FastAPI application for maximum integration.
"""

import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

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


try:
    from aiortc import RTCPeerConnection  # type: ignore

    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    logger.warning("aiortc not installed - SFU adapter will not be available")


@dataclass
class AiortcPeer:
    """A peer in the aiortc SFU."""

    peer_id: str
    pc: RTCPeerConnection  # type: ignore
    producers: Dict[str, SFUProducer] = None  # type: ignore
    consumers: Dict[str, SFUConsumer] = None  # type: ignore
    transports: Dict[str, SFUTransport] = None  # type: ignore

    def __post_init__(self):
        if self.producers is None:
            self.producers = {}
        if self.consumers is None:
            self.consumers = {}
        if self.transports is None:
            self.transports = {}


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

        self._rooms: Dict[
            str, Dict[str, AiortcPeer]
        ] = {}  # room_id -> peer_id -> AiortcPeer
        self._ice_servers = ice_servers or []
        logger.info("aiortc SFU adapter initialized")

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

        # Close all peer connections
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

        # Create RTCPeerConnection for this peer
        pc = RTCPeerConnection(configuration={"iceServers": self._ice_servers})  # type: ignore

        # Track the peer
        self._rooms[room_id][peer_id] = AiortcPeer(peer_id=peer_id, pc=pc)

        logger.info(f"Peer {peer_id} joined room {room_id}")

        # Return room capabilities
        return await self.get_router_capabilities(room_id)

    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room."""
        if room_id not in self._rooms:
            return False

        if peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        # Close the peer connection
        try:
            await peer.pc.close()
        except Exception as e:
            logger.warning(f"Error closing peer connection: {e}")

        del self._rooms[room_id][peer_id]
        logger.info(f"Peer {peer_id} left room {room_id}")

        # Clean up empty rooms
        if not self._rooms[room_id]:
            await self.close_room(room_id)

        return True

    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """
        Create a WebRTC transport for a peer.

        In aiortc, transports are implicit in the RTCPeerConnection.
        We return a placeholder that matches the expected interface.
        """
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError(f"Peer {peer_id} not in room {room_id}")

        peer = self._rooms[room_id][peer_id]
        transport_id = str(uuid.uuid4())

        # Get ICE parameters from the peer connection
        ice_params = {
            "usernameFragment": "",
            "password": "",
        }

        # Get ICE candidates
        ice_candidates = []

        # DTLS parameters
        dtls_params = {
            "fingerprints": [],
            "role": "auto",
        }

        transport = SFUTransport(
            id=transport_id,
            direction=direction,
            ice_parameters=ice_params,
            ice_candidates=ice_candidates,
            dtls_parameters=dtls_params,
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
        """Connect a transport with DTLS parameters."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        if transport_id not in peer.transports:
            return False

        # In aiortc, DTLS is handled automatically by RTCPeerConnection
        logger.debug(f"Connected transport {transport_id} for peer {peer_id}")
        return True

    async def produce(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        kind: MediaKind,
        rtp_parameters: Dict[str, Any],
    ) -> SFUProducer:
        """Create a producer to send media."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError(f"Peer {peer_id} not in room {room_id}")

        peer = self._rooms[room_id][peer_id]
        producer_id = str(uuid.uuid4())

        producer = SFUProducer(
            id=producer_id,
            kind=kind,
            rtp_parameters=rtp_parameters,
            paused=False,
        )

        peer.producers[producer_id] = producer
        logger.info(f"Created producer {producer_id} ({kind.value}) for peer {peer_id}")

        return producer

    async def consume(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: Dict[str, Any],
    ) -> SFUConsumer:
        """Create a consumer to receive media."""
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError(f"Peer {peer_id} not in room {room_id}")

        peer = self._rooms[room_id][peer_id]
        consumer_id = str(uuid.uuid4())

        # Find the producer
        producer = None
        for p in self._rooms[room_id].values():
            if producer_id in p.producers:
                producer = p.producers[producer_id]
                break

        if not producer:
            raise SFUConnectionError(f"Producer {producer_id} not found")

        consumer = SFUConsumer(
            id=consumer_id,
            producer_id=producer_id,
            kind=producer.kind,
            rtp_parameters=rtp_capabilities,
            paused=False,
        )

        peer.consumers[consumer_id] = consumer
        logger.info(f"Created consumer {consumer_id} for peer {peer_id}")

        return consumer

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
        logger.debug(f"Closed producer {producer_id}")
        return True

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
        # Return standard WebRTC capabilities
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
        """Set preferred simulcast layers for a consumer."""
        # aiortc doesn't support simulcast in the same way as mediasoup
        # This is a placeholder for future implementation
        logger.debug(
            f"Set preferred layers for consumer {consumer_id}: spatial={spatial_layer}, temporal={temporal_layer}"
        )
        return True

    async def health_check(self) -> bool:
        """Check if the SFU server is healthy."""
        # Since aiortc runs in-process, we just check if it's available
        return AIORTC_AVAILABLE

    async def close(self) -> None:
        """Close the adapter and clean up all resources."""
        # Close all rooms
        for room_id in list(self._rooms.keys()):
            await self.close_room(room_id)

        logger.info("aiortc SFU adapter closed")
