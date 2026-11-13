"""Connection state for the mediasoup WebSocket (protoo) adapter."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..base import SFUConsumer, SFUProducer


@dataclass
class PendingRequest:
    """A pending protoo request."""

    id: int
    method: str
    future: asyncio.Future
    created_at: float


@dataclass
class PeerConnection:
    """Represents a peer's connection to a room."""

    peer_id: str
    room_id: str
    websocket: Any  # websockets.WebSocketClientProtocol
    request_id: int = 0
    pending_requests: Dict[int, PendingRequest] = field(default_factory=dict)
    send_transport_id: Optional[str] = None
    recv_transport_id: Optional[str] = None
    producers: Dict[str, SFUProducer] = field(default_factory=dict)
    consumers: Dict[str, SFUConsumer] = field(default_factory=dict)
    rtp_capabilities: Optional[Dict[str, Any]] = None
    joined: bool = False
