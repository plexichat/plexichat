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

import utils.logger as logger

from .batching import (
    OutboundFrameBatcher,
    DEFAULT_FLUSH_INTERVAL_S,
    DEFAULT_MAX_BATCH,
    DEFAULT_QUEUE_SIZE,
    DEFAULT_SEND_TIMEOUT_S,
)
from .rendering import PayloadRenderCache
from src.core.events.snowflakes import stringify_snowflake_ids


#: Upper bound on the per-connection cache of JSON-serialized frame bytes.
#: Frames rendered by ``PayloadRenderCache`` are shared objects, so repeated
#: identical payloads (typing re-asserts, presence updates) reuse the same dict
#: and only need to be serialized once. The cache is a bounded FIFO and is
#: keyed by object identity (with an identity check on lookup) to stay correct.
MAX_SERIALIZE_CACHE_ENTRIES = 16


#: Frame types whose ``d`` carries the message body as an OPTIONAL field on top
#: of a base-authorized payload. Delivery of these frames is gated by a base
#: message intent (GUILD_MESSAGES / DIRECT_MESSAGES -- see the MESSAGE_ACK /
#: MESSAGE_BOOKMARK_* gate in ``intents._compute_filter_decision``), but the
#: embedded body is the separate privileged MESSAGE_CONTENT payload. Without a
#: second, per-frame gate a connection holding ONLY the base intent still
#: receives the private text. This mirrors the GUILD_MEMBERS_CHUNK presence
#: gate in ``handlers/guild.py``.
_PRIVILEGED_CONTENT_FIELDS_BY_EVENT = {
    "MESSAGE_BOOKMARK_ADD": ("message_content",),
    "MESSAGE_BOOKMARK_REMOVE": ("message_content",),
    # Mention notifications carry the mentioned message's body as a preview on
    # an otherwise metadata-only frame. NOTIFICATION_CREATE is dispatched with
    # no scope and no base intent (router default-allow), so without this gate
    # a connection that never declared MESSAGE_CONTENT still reads the text.
    "NOTIFICATION_CREATE": ("content", "content_preview"),
}


def _scrub_privileged_frame_fields(data: Any, intents: Any) -> Any:
    """Blank privileged fields embedded in an otherwise authorized frame.

    Applied in ``Connection.send_json`` -- the last hop before the wire -- so
    every delivery path (live fan-out, single-connection dispatch, raw frames
    and RESUME replay) is covered without trusting any one emitter. Only the
    embedded field is blanked; the base event itself was legitimately
    authorized by its base intent, so clients still learn that a bookmark was
    added/removed. An absent/invalid intent mask fails closed, and an
    unparseable scope is treated as guild (MESSAGE_CONTENT), never as a
    friendlier DM gate.
    """
    if not isinstance(data, dict):
        return data
    fields = _PRIVILEGED_CONTENT_FIELDS_BY_EVENT.get(data.get("t"))
    if not fields:
        return data
    payload = data.get("d")
    if not isinstance(payload, dict) or not any(key in payload for key in fields):
        return data

    def _as_id(value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    server_id = _as_id(payload.get("server_id"))
    if server_id is None:
        server_id = _as_id(payload.get("guild_id"))
    channel_id = _as_id(payload.get("channel_id"))
    is_dm = server_id is None and channel_id is not None

    from .intents import should_include_message_content, validate_intents

    if validate_intents(intents) and should_include_message_content(intents, is_dm):
        return data
    scrubbed = dict(payload)
    for key in fields:
        scrubbed[key] = None
    out = dict(data)
    out["d"] = scrubbed
    return out


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
    # Gateway replay session id (hex string from ``generate_session_id``). Do
    # NOT confuse this with ``auth_session_id`` below.
    session_id: Optional[str] = None
    # The *auth* session id (``auth_sessions.id``) this socket authenticated
    # under at IDENTIFY/RESUME. Bound from the verified ``TokenInfo.session_id``
    # so a single-session revoke can close only this session's sockets instead
    # of blanket-closing every socket of the user.
    auth_session_id: Optional[int] = None
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
    _outbound_batcher: Optional[OutboundFrameBatcher] = field(default=None, repr=False)
    _batcher_task: Optional[asyncio.Task] = field(default=None, repr=False)
    # Per-connection rendering cache. Frames rendered outside a fan-out (inline
    # ACKs, raw opcodes, resume responses) reuse their snowflake rendering
    # instead of re-walking an identical payload on every send.
    _render_cache: PayloadRenderCache = field(
        default_factory=PayloadRenderCache, repr=False
    )
    # Ceiling for one ASGI send. A slow/stopped reader must not be able to pin
    # this connection's writer (or the batch flush task that uses it) forever.
    _send_timeout: float = DEFAULT_SEND_TIMEOUT_S
    # Observable back-pressure counter: writes abandoned because the peer did
    # not accept a frame within ``_send_timeout``.
    _send_timeouts: int = field(default=0, repr=False)
    # Bounded FIFO cache: id(frame) -> (frame, serialized_utf8_bytes). Avoids
    # re-running json.dumps for the same rendered frame object on compressed
    # sends. Holding a strong ref to the frame prevents id() reuse; the identity
    # check on lookup is a second guard. Frames are treated as immutable once
    # enqueued (the render cache already relies on that).
    _serialize_cache: Dict[int, tuple] = field(default_factory=dict, repr=False)

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

        data = _scrub_privileged_frame_fields(data, self.intents)

        batcher = self._outbound_batcher
        if batcher is not None:
            return batcher.enqueue(data)

        return await self._write_frames([data])

    def _serialize_frame(self, frame: Dict[str, Any]) -> bytes:
        """Return the UTF-8 JSON bytes for ``frame``, caching shared objects.

        Compressed frames are serialized per message (each still needs its own
        zlib sync-flush boundary), but the JSON encoding itself is pure. Rendered
        gateway frames are shared objects, so a burst of identical payloads
        would otherwise run ``json.dumps`` once per frame. Cache by identity in
        a bounded FIFO; the cache holds a strong reference so ``id()`` cannot be
        recycled while an entry lives, and the identity check guards lookup.
        """
        import json

        key = id(frame)
        cached = self._serialize_cache.get(key)
        if cached is not None and cached[0] is frame:
            return cached[1]

        encoded = json.dumps(frame).encode("utf-8")
        if len(self._serialize_cache) >= MAX_SERIALIZE_CACHE_ENTRIES:
            # dict preserves insertion order: drop the oldest entry.
            self._serialize_cache.pop(next(iter(self._serialize_cache)))
        self._serialize_cache[key] = (frame, encoded)
        return encoded

    async def _write_frames(self, frames: List[Dict[str, Any]]) -> bool:
        """Write a batch of frames in order under one send-lock acquisition.

        Each frame remains an independent WebSocket message; coalescing only
        removes the per-frame lock/scheduler overhead. The writer is shared by
        the direct path and the batch flush task so they cannot interleave.
        """
        if self.state in (ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED):
            return False
        timed_out = False
        try:
            async with self._send_lock:
                for index, data in enumerate(frames):
                    try:
                        if self.compress and self._zlib_context:
                            json_bytes = self._serialize_frame(data)
                            compressed = self._zlib_context.compress(json_bytes)
                            compressed += self._zlib_context.flush(zlib.Z_SYNC_FLUSH)
                            await asyncio.wait_for(
                                self.websocket.send_bytes(compressed),
                                timeout=self._send_timeout,
                            )
                        else:
                            await asyncio.wait_for(
                                self.websocket.send_json(data),
                                timeout=self._send_timeout,
                            )
                    except asyncio.TimeoutError:
                        # Slow-consumer defense: the ASGI send is suspended
                        # waiting for the peer's read buffer. Never let one
                        # stalled socket pin the writer (and the batch flush
                        # task that shares it) indefinitely. Abandon the rest
                        # of this batch: the peer is not accepting frames, so
                        # retrying the remaining ones only burns time.
                        timed_out = True
                        self._send_timeouts += 1
                        logger.warning(
                            "Outbound send timed out for connection %s after "
                            "%.3fs (slow consumer); dropping %d frame(s)",
                            self.connection_id,
                            self._send_timeout,
                            len(frames) - index - 1,
                        )
                        if self.compress:
                            self._zlib_context = zlib.compressobj()
                        break
            return not timed_out
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
        """Best-effort flush of accepted frames before close.

        Bounded by the batcher's drain deadline; on a stalled consumer the
        remaining queued frames are shed rather than hanging teardown.
        """
        batcher = self._outbound_batcher
        if batcher is not None:
            await batcher.drain()

    async def stop_batching(self) -> None:
        """Cancel the flush task after a final, bounded best-effort drain."""
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
