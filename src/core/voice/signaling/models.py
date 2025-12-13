"""
Signaling models - Dataclasses for WebRTC signaling entities.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class SDPType(Enum):
    """SDP message types."""
    OFFER = "offer"
    ANSWER = "answer"
    PRANSWER = "pranswer"
    ROLLBACK = "rollback"


class SignalingState(Enum):
    """WebRTC signaling connection states."""
    NEW = "new"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class QualityLevel(Enum):
    """Connection quality levels."""
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    CRITICAL = "critical"


@dataclass
class SDPMessage:
    """SDP (Session Description Protocol) message."""
    sdp_type: SDPType
    sdp: str
    session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.sdp_type.value,
            "sdp": self.sdp,
            "session_id": self.session_id,
        }


@dataclass
class ICECandidate:
    """ICE (Interactive Connectivity Establishment) candidate."""
    candidate: str
    sdp_mid: Optional[str] = None
    sdp_mline_index: Optional[int] = None
    username_fragment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "candidate": self.candidate,
            "sdpMid": self.sdp_mid,
            "sdpMLineIndex": self.sdp_mline_index,
            "usernameFragment": self.username_fragment,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ICECandidate":
        """Create from dictionary."""
        return cls(
            candidate=data.get("candidate", ""),
            sdp_mid=data.get("sdpMid") or data.get("sdp_mid"),
            sdp_mline_index=data.get("sdpMLineIndex") or data.get("sdp_mline_index"),
            username_fragment=data.get("usernameFragment") or data.get("username_fragment"),
        )


@dataclass
class TURNCredentials:
    """TURN server credentials with time-limited authentication."""
    username: str
    credential: str
    urls: List[str]
    ttl: int
    expires_at: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "username": self.username,
            "credential": self.credential,
            "urls": self.urls,
            "ttl": self.ttl,
            "expires_at": self.expires_at,
        }


@dataclass
class ICEServer:
    """ICE server configuration (STUN or TURN)."""
    urls: List[str]
    username: Optional[str] = None
    credential: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: Dict[str, Any] = {"urls": self.urls}
        if self.username:
            result["username"] = self.username
        if self.credential:
            result["credential"] = self.credential
        return result


@dataclass
class VoiceServerInfo:
    """Voice server connection information."""
    endpoint: str
    token: str
    ice_servers: List[ICEServer]
    session_id: str
    channel_id: int
    user_id: int
    bitrate: int = 64000

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "endpoint": self.endpoint,
            "token": self.token,
            "ice_servers": [s.to_dict() for s in self.ice_servers],
            "session_id": self.session_id,
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "bitrate": self.bitrate,
        }


@dataclass
class ConnectionQuality:
    """Connection quality metrics."""
    user_id: int
    channel_id: int
    quality_level: QualityLevel
    bitrate: int
    packet_loss: float
    jitter: float
    round_trip_time: int
    timestamp: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "quality_level": self.quality_level.value,
            "bitrate": self.bitrate,
            "packet_loss": self.packet_loss,
            "jitter": self.jitter,
            "round_trip_time": self.round_trip_time,
            "timestamp": self.timestamp,
        }


@dataclass
class ScreenShareState:
    """Screen sharing state for a user."""
    user_id: int
    channel_id: int
    active: bool
    stream_id: Optional[str] = None
    started_at: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "active": self.active,
            "stream_id": self.stream_id,
            "started_at": self.started_at,
        }


@dataclass
class VoiceConnection:
    """Active voice connection state."""
    user_id: int
    channel_id: int
    session_id: str
    state: SignalingState
    created_at: int
    last_activity: int
    ice_candidates: List[ICECandidate] = field(default_factory=list)
    local_sdp: Optional[str] = None
    remote_sdp: Optional[str] = None
    screen_share: Optional[ScreenShareState] = None
    quality: Optional[ConnectionQuality] = None
    # SFU-related fields
    transport_id: Optional[str] = None
    sfu_transport_id: Optional[str] = None
    sfu_room_id: Optional[str] = None
    sfu_peer_id: Optional[str] = None
    sfu_producer_ids: List[str] = field(default_factory=list)
    sfu_consumer_ids: List[str] = field(default_factory=list)


# Quality thresholds for bitrate adjustment
QUALITY_BITRATE_THRESHOLDS = {
    QualityLevel.EXCELLENT: {"min": 128000, "max": 384000},
    QualityLevel.GOOD: {"min": 64000, "max": 128000},
    QualityLevel.FAIR: {"min": 32000, "max": 64000},
    QualityLevel.POOR: {"min": 16000, "max": 32000},
    QualityLevel.CRITICAL: {"min": 8000, "max": 16000},
}
