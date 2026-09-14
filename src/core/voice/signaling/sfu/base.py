"""
SFU base adapter - Abstract base class for SFU integrations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

import utils.logger as logger

from .quality import canonical_channel_id, canonical_user_id


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
class AcousticDefenseConfig:
    """Configuration for acoustic eavesdropping defenses."""

    enabled: bool = False
    jitter_ms_min: float = 5.0
    jitter_ms_max: float = 20.0
    spectral_masking: bool = True
    spectral_mask_noise_db: float = -40.0
    spectral_mask_low_hz: float = 1000.0
    spectral_mask_high_hz: float = 8000.0
    vad_gating: bool = True
    vad_speech_threshold: float = 0.02
    vad_silence_frames: int = 3
    transient_shaving: bool = True
    transient_attack_ms: float = 0.5
    transient_release_ms: float = 5.0
    transient_ratio: float = 0.3


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

    async def complete_join(
        self,
        room_id: str,
        peer_id: str,
        rtp_capabilities: Dict[str, Any],
        display_name: Optional[str] = None,
    ) -> Any:
        """
        Optional: Complete the join process.
        Used by some SFUs like mediasoup-demo.
        """
        return True

    async def apply_remote_offer(
        self,
        room_id: str,
        peer_id: str,
        offer_sdp: str,
    ) -> Dict[str, Any]:
        """
        Apply the client's WebRTC offer to the peer's connection.

        For aiortc this MUST call ``setRemoteDescription`` so the peer's
        DTLS/ICE state machine knows the remote side; without it the
        negotiated description never completes and no media can flow.
        Adapters that negotiate out-of-band (mediasoup) may ignore the SDP.

        Returns a dict with the peer's local description ("sdp", "type")
        where applicable.
        """
        return {}

    async def apply_remote_answer(
        self,
        room_id: str,
        peer_id: str,
        answer_sdp: str,
    ) -> bool:
        """
        Apply the client's SDP answer to the peer's connection.

        For aiortc this completes DTLS handshake setup. No-op for
        out-of-band SFUs.
        """
        return True

    async def add_ice_candidate(
        self,
        room_id: str,
        peer_id: str,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """Deliver a remote (client) ICE candidate to the media plane.

        Trickle ICE sends candidates out of band from the offer/answer, so an
        adapter that terminates the media session itself MUST feed them to its
        peer connection or it never learns the client's address and can only
        connect when peer-reflexive discovery happens to succeed. Adapters that
        negotiate their transport out of band (mediasoup, Janus) have nothing
        to do here and return ``True`` as a no-op.

        Returns whether the candidate was accepted.
        """
        return True

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
        """Set preferred simulcast layers for a consumer.

        ``consumer_id`` may be empty, which is how ``BandwidthEstimator``
        calls this: it keys peers by channel/user and has no consumer mapping,
        so an empty id means "every consumer this peer receives" (the degraded
        peer's own downlink is what the estimator is reacting to).

        Returns True only when at least one layer request was actually issued.
        A backend that has no simulcast API should still apply the nearest
        lever it does have (aiortc, for example, bounds the peer's outbound
        video to a low frame rate and re-attaches full rate when the hint is
        lifted; it exposes no bitrate API) and must return False -- rather
        than report success it did not achieve -- when there was nothing to
        change, such as a peer with no video or no consumers.
        """
        pass

    @abstractmethod
    async def start_recording(self, room_id: str, output_dir: str) -> Dict[str, Any]:
        """Start recording audio in a room.

        Args:
            room_id: Room identifier
            output_dir: Directory to store recording files

        Returns:
            Dict with recording metadata (e.g. recording_id, file_count)
        """
        pass

    @abstractmethod
    async def stop_recording(self, room_id: str) -> Optional[List[str]]:
        """Stop recording and return recorded file paths.

        Args:
            room_id: Room identifier

        Returns:
            List of recorded file paths, or None if not recording
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the SFU server is healthy."""
        pass

    def set_quality_mixin(self, quality_mixin: Any) -> None:
        """Retain the quality sink for SFU-side metric reporting.

        Push-based adapters call :meth:`report_quality` from their receive
        loop; request/response adapters override this to additionally start a
        :class:`~.quality.QualityReporter` feeding from ``collect_quality``.
        """
        self._quality_mixin = quality_mixin

    def report_quality(
        self, room_id: Any, peer_id: Any, metrics: Dict[str, Any]
    ) -> None:
        """Forward one SFU-side metric sample to the canonical quality store.

        ``room_id`` / ``peer_id`` are SFU-scoped identifiers and are mapped
        back to the numeric channel/user ids the store is keyed by. Samples
        whose identifiers cannot be mapped are dropped, and a failing sink
        never raises into a media-path coroutine.
        """
        mixin = getattr(self, "_quality_mixin", None)
        if mixin is None:
            return
        channel_id = canonical_channel_id(room_id)
        user_id = canonical_user_id(peer_id)
        if channel_id is None or user_id is None:
            logger.debug(
                f"Ignoring SFU quality sample with unmappable ids: "
                f"room={room_id!r} peer={peer_id!r}"
            )
            return
        payload = {key: value for key, value in metrics.items() if value is not None}
        if not payload:
            return
        try:
            mixin.update_quality_hint(channel_id, user_id, **payload)
        except Exception as exc:  # never let a metric update kill the media session
            logger.debug(
                f"Failed to record SFU quality for channel {channel_id} "
                f"user {user_id}: {exc}"
            )

    async def collect_quality(
        self,
    ) -> List[Tuple[Any, Any, Dict[str, Any]]]:
        """Return ``(room_id, peer_id, metrics)`` samples for pull backends.

        Default is empty: adapters with no pollable stat source report nothing
        rather than fabricating samples.
        """
        return []

    async def close(self) -> None:
        """Release resources held by the adapter.

        Subclasses that own network sessions (e.g. aiohttp) should override
        this to close them explicitly, preventing ResourceWarning on GC.
        """
        pass
