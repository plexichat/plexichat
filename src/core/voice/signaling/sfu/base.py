"""
SFU base adapter - Abstract base class for SFU integrations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class TransportDirection(Enum):
    """Transport direction for WebRTC."""
    SEND = "send"
    RECV = "recv"
    SENDRECV = "sendrecv"


class MediaKind(Enum):
    """Media types."""
    AUDIO = "audio"
    VIDEO = "video"


@dataclass
class SFUTransport:
    """WebRTC transport on the SFU."""
    id: str
    direction: TransportDirection
    ice_parameters: Dict[str, Any]
    ice_candidates: List[Dict[str, Any]]
    dtls_parameters: Dict[str, Any]
    sctp_parameters: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "id": self.id,
            "direction": self.direction.value,
            "iceParameters": self.ice_parameters,
            "iceCandidates": self.ice_candidates,
            "dtlsParameters": self.dtls_parameters,
        }
        if self.sctp_parameters:
            result["sctpParameters"] = self.sctp_parameters
        return result


@dataclass
class SFUProducer:
    """Media producer on the SFU (sends media)."""
    id: str
    kind: MediaKind
    rtp_parameters: Dict[str, Any]
    paused: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "rtpParameters": self.rtp_parameters,
            "paused": self.paused,
        }


@dataclass
class SFUConsumer:
    """Media consumer on the SFU (receives media)."""
    id: str
    producer_id: str
    kind: MediaKind
    rtp_parameters: Dict[str, Any]
    paused: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "producerId": self.producer_id,
            "kind": self.kind.value,
            "rtpParameters": self.rtp_parameters,
            "paused": self.paused,
        }


@dataclass
class RoomInfo:
    """Information about an SFU room."""
    id: str
    peers: List[str] = field(default_factory=list)
    producers: List[str] = field(default_factory=list)


class SFUAdapter(ABC):
    """Abstract base class for SFU adapters."""
    
    @abstractmethod
    async def create_room(self, room_id: str) -> RoomInfo:
        """
        Create a new room on the SFU.
        
        Args:
            room_id: Unique room identifier
            
        Returns:
            RoomInfo for the created room
        """
        pass
    
    @abstractmethod
    async def close_room(self, room_id: str) -> bool:
        """
        Close a room on the SFU.
        
        Args:
            room_id: Room identifier
            
        Returns:
            True if closed successfully
        """
        pass
    
    @abstractmethod
    async def join_room(self, room_id: str, peer_id: str) -> Dict[str, Any]:
        """
        Join a peer to a room.
        
        Args:
            room_id: Room identifier
            peer_id: Peer identifier
            
        Returns:
            Room capabilities and existing producers
        """
        pass
    
    @abstractmethod
    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """
        Remove a peer from a room.
        
        Args:
            room_id: Room identifier
            peer_id: Peer identifier
            
        Returns:
            True if left successfully
        """
        pass
    
    @abstractmethod
    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """
        Create a WebRTC transport for a peer.
        
        Args:
            room_id: Room identifier
            peer_id: Peer identifier
            direction: Transport direction
            
        Returns:
            SFUTransport with connection parameters
        """
        pass
    
    @abstractmethod
    async def connect_transport(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        dtls_parameters: Dict[str, Any],
    ) -> bool:
        """
        Connect a transport with DTLS parameters.
        
        Args:
            room_id: Room identifier
            peer_id: Peer identifier
            transport_id: Transport identifier
            dtls_parameters: DTLS parameters from client
            
        Returns:
            True if connected successfully
        """
        pass
    
    @abstractmethod
    async def produce(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        kind: MediaKind,
        rtp_parameters: Dict[str, Any],
    ) -> SFUProducer:
        """
        Create a producer to send media.
        
        Args:
            room_id: Room identifier
            peer_id: Peer identifier
            transport_id: Transport identifier
            kind: Media kind (audio/video)
            rtp_parameters: RTP parameters from client
            
        Returns:
            SFUProducer
        """
        pass
    
    @abstractmethod
    async def consume(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: Dict[str, Any],
    ) -> SFUConsumer:
        """
        Create a consumer to receive media.
        
        Args:
            room_id: Room identifier
            peer_id: Peer identifier
            transport_id: Transport identifier
            producer_id: Producer to consume
            rtp_capabilities: Client RTP capabilities
            
        Returns:
            SFUConsumer
        """
        pass
    
    @abstractmethod
    async def pause_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Pause a producer."""
        pass
    
    @abstractmethod
    async def resume_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Resume a producer."""
        pass
    
    @abstractmethod
    async def close_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Close a producer."""
        pass
    
    @abstractmethod
    async def get_room_info(self, room_id: str) -> Optional[RoomInfo]:
        """Get information about a room."""
        pass
    
    @abstractmethod
    async def get_router_capabilities(self, room_id: str) -> Dict[str, Any]:
        """Get RTP capabilities for a room's router."""
        pass
    
    @abstractmethod
    async def set_preferred_layers(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
        spatial_layer: int,
        temporal_layer: int,
    ) -> bool:
        """Set preferred simulcast layers for a consumer."""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the SFU server is healthy."""
        pass
