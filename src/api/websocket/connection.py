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

from .batching import (
    OutboundFrameBatcher,
    DEFAULT_FLUSH_INTERVAL_S,
    DEFAULT_MAX_BATCH,
    DEFAULT_QUEUE_SIZE,
)
from .rendering import PayloadRenderCache
from src.core.events.snowflakes import stringify_snowflake_ids


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
    is_selftest: bool = False
    _zlib_context: Optional[Any] = field(default=None, repr=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Per-connection inbound throttle state: opcode -> monotonic timestamps.
    _inbound_rate_buckets: Dict[int, List[float]] = field(
        default_factory=dict, repr=False
    )
    # Optional coalescing outbound batcher. Disabled by default so the many
    # callers that construct a bare Connection keep their synchronous
    # write-through behaviour; gateway_endpoint opts in for live sockets.
    _outbound_batcher: Optional[OutboundFrameBatcher] = field(
        default=None, repr=False
    )
    _batcher_task: Optional[asyncio.Task] = field(default=None, repr=False)
    # Per-connection rendering cache. Frames rendered outside a fan-out (inline
    # ACKs, raw opcodes, resume responses) reuse their snowflake rendering
    # instead of re-walking an identical payload on every send.
    _render_cache: PayloadRenderCache = field(
        default_factory=PayloadRenderCache, repr=False
    )

    def __post_init__(self) -> None:
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
        """Server-side heartbeat receive->ACK-send interval, in milliseconds.

        This is NOT a client round-trip time (the server has no mirrored
        clock to measure that); it is how long the server took to answer the
        last heartbeat, recorded when the ACK is written. Clients that need
        RTT measure it themselves.
        """
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
        """Send JSON data to the WebSocket.

        When :meth:`enable_batching` has been called, the frame is queued for
        the coalescing flush task and ``True`` means "accepted by the bounded
        queue" (a full queue sheds the frame and returns ``False``). Without
        batching, the frame is written immediately and ``True`` means the
        transport accepted it.
        """
        if self.state in (ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED):
            return False

        batcher = self._outbound_batcher
        if batcher is not None:
            return batcher.enqueue(data)

        return await self._write_frames([data])

    async def _write_frames(self, frames: List[Dict[str, Any]]) -> bool:
        """Write a batch of frames in order under one send-lock acquisition.

        Each frame remains an independent WebSocket message; coalescing only
        removes the per-frame lock/scheduler overhead. The writer is shared by
        the direct path and the batch flush task so they cannot interleave.
        """
        if self.state in (ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED):
            return False
        try:
            async with self._send_lock:
                for data in frames:
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
            # C6: a partially-failed compressed send leaves the shared zlib
            # context mid-stream; reusing it would emit a corrupt deflate
            # stream and desync the client decompressor on every subsequent
            # binary frame. Reset the context so the next frame starts from a
            # clean deflate boundary (the client falls back to a reconnect on
            # persistent failures).
            if self.compress:
                self._zlib_context = zlib.compressobj()
            return False

    def enable_batching(
        self,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL_S,
        max_batch: int = DEFAULT_MAX_BATCH,
    ) -> OutboundFrameBatcher:
        """Opt this connection into coalesced outbound frame flushing.

        Must be called from inside the serving event loop (it starts the flush
        task). Idempotent: repeated calls return the existing batcher.
        """
        if self._outbound_batcher is None:
            batcher = OutboundFrameBatcher(
                self._write_frames,
                queue_size=queue_size,
                flush_interval=flush_interval,
                max_batch=max_batch,
            )
            self._outbound_batcher = batcher
            self._batcher_task = asyncio.create_task(batcher.run())
        return self._outbound_batcher

    async def flush_outbound(self) -> None:
        """Flush every accepted-but-unwritten frame (e.g. before close)."""
        batcher = self._outbound_batcher
        if batcher is not None:
            await batcher.drain()

    async def stop_batching(self) -> None:
        """Cancel the flush task after a final best-effort drain."""
        batcher = self._outbound_batcher
        task = self._batcher_task
        self._batcher_task = None
        self._outbound_batcher = None
        # Drain explicitly: the task may not have started yet (cancel before
        # its first tick would otherwise skip its own CancelledError drain).
        if batcher is not None:
            await batcher.drain()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @property
    def outbound_batcher(self) -> Optional[OutboundFrameBatcher]:
        """The active batcher (used by tests/observability), if any."""
        return self._outbound_batcher

    def render_dispatch_data(self, data: Any) -> Any:
        """Return the snowflake-rendered form of ``data`` for this connection.

        Identical payloads (periodic presence re-asserts, repeated typing
        payloads) are rendered once per connection and reused thereafter.
        """
        return self._render_cache.render(data, stringify_snowflake_ids)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the connection.

        Acquires ``_send_lock`` so a final in-flight frame cannot interleave
        AFTER the close frame (C5): without the lock, ``close()`` could race
        a concurrent ``send_json`` and emit a frame past the close handshake.
        """
        self.set_disconnecting()
        try:
            async with self._send_lock:
                await self.websocket.close(code, reason)
        except Exception as close_error:
            # The peer is already gone; the socket state below is authoritative.
            import utils.logger as logger

            logger.debug(f"websocket close failed: {close_error}")
        self.set_disconnected()

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
