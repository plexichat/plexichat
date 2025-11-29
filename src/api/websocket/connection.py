"""
Connection state management - Tracks individual WebSocket connections.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List
import time
import asyncio
import zlib

from fastapi import WebSocket


class ConnectionState(Enum):
    """Connection lifecycle states."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    IDENTIFYING = "identifying"
    READY = "ready"
    RESUMING = "resuming"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"


@dataclass
class Connection:
    """Represents a single WebSocket connection."""
    websocket: WebSocket
    connection_id: str
    state: ConnectionState = ConnectionState.CONNECTING
    user_id: Optional[int] = None
    session_id: Optional[str] = None
    sequence: int = 0
    intents: int = 0
    compress: bool = False
    last_heartbeat: float = field(default_factory=time.monotonic)
    last_heartbeat_ack: float = field(default_factory=time.monotonic)
    heartbeat_interval_ms: int = 45000
    connected_at: float = field(default_factory=time.monotonic)
    identified_at: Optional[float] = None
    event_count: int = 0
    event_window_start: float = field(default_factory=time.monotonic)
    missed_heartbeats: int = 0
    properties: Dict[str, Any] = field(default_factory=dict)
    _zlib_context: Optional[zlib.compressobj] = field(default=None, repr=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def __post_init__(self):
        if self.compress and self._zlib_context is None:
            self._zlib_context = zlib.compressobj()

    @property
    def is_authenticated(self) -> bool:
        """Check if connection is authenticated."""
        return self.user_id is not None and self.state == ConnectionState.READY

    @property
    def is_alive(self) -> bool:
        """Check if connection is still alive based on heartbeat."""
        if self.state in (ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED):
            return False
        now = time.monotonic()
        timeout = (self.heartbeat_interval_ms / 1000) * 2
        return (now - self.last_heartbeat) < timeout

    @property
    def latency_ms(self) -> float:
        """Get connection latency in milliseconds."""
        return (self.last_heartbeat_ack - self.last_heartbeat) * 1000

    def record_heartbeat(self) -> None:
        """Record that a heartbeat was received."""
        self.last_heartbeat = time.monotonic()
        self.missed_heartbeats = 0

    def record_heartbeat_ack(self) -> None:
        """Record that a heartbeat ACK was sent."""
        self.last_heartbeat_ack = time.monotonic()

    def increment_sequence(self) -> int:
        """Increment and return the sequence number."""
        self.sequence += 1
        return self.sequence

    def check_rate_limit(self, limit_per_minute: int) -> bool:
        """
        Check if connection is within rate limits.

        Args:
            limit_per_minute: Maximum events per minute

        Returns:
            True if within limits, False if rate limited
        """
        now = time.monotonic()
        if now - self.event_window_start >= 60:
            self.event_count = 0
            self.event_window_start = now

        if self.event_count >= limit_per_minute:
            return False

        self.event_count += 1
        return True

    async def send_json(self, data: Dict[str, Any]) -> bool:
        """
        Send JSON data to the WebSocket.

        Args:
            data: Data to send

        Returns:
            True if sent successfully
        """
        if self.state in (ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED):
            return False

        try:
            async with self._send_lock:
                if self.compress and self._zlib_context:
                    import json
                    json_bytes = json.dumps(data).encode("utf-8")
                    compressed = self._zlib_context.compress(json_bytes)
                    compressed += self._zlib_context.flush(zlib.Z_SYNC_FLUSH)
                    await self.websocket.send_bytes(compressed)
                else:
                    await self.websocket.send_json(data)
            return True
        except Exception:
            return False

    def set_identified(
        self,
        user_id: int,
        session_id: str,
        intents: int,
    ) -> None:
        """
        Mark connection as identified.

        Args:
            user_id: Authenticated user ID
            session_id: Session ID
            intents: Gateway intents
        """
        self.user_id = user_id
        self.session_id = session_id
        self.intents = intents
        self.state = ConnectionState.READY
        self.identified_at = time.monotonic()

    def set_resuming(self) -> None:
        """Mark connection as resuming."""
        self.state = ConnectionState.RESUMING

    def set_disconnecting(self) -> None:
        """Mark connection as disconnecting."""
        self.state = ConnectionState.DISCONNECTING

    def set_disconnected(self) -> None:
        """Mark connection as disconnected."""
        self.state = ConnectionState.DISCONNECTED

    def enable_compression(self) -> None:
        """Enable zlib compression for this connection."""
        self.compress = True
        self._zlib_context = zlib.compressobj()

    def to_dict(self) -> Dict[str, Any]:
        """Convert connection info to dictionary."""
        return {
            "connection_id": self.connection_id,
            "state": self.state.value,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "intents": self.intents,
            "compress": self.compress,
            "connected_at": self.connected_at,
            "identified_at": self.identified_at,
            "latency_ms": self.latency_ms if self.is_authenticated else None,
        }
