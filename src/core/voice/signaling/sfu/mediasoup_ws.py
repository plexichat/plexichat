"""
Mediasoup WebSocket SFU adapter - Integration with mediasoup-demo server.

Uses WebSocket (protoo protocol) for signaling instead of REST API.
This adapter is designed to work with the mediasoup-demo server.
"""

import asyncio
import hashlib
import hmac
import json
import os
import ssl
from typing import Dict, Optional, Any, Callable, cast
from dataclasses import dataclass, field

import utils.logger as logger
import utils.config as _voice_config  # noqa: F401


from ..exceptions import SFUConnectionError, SFUTimeoutError
from .base import (
    SFUAdapter,
    SFUTransport,
    SFUProducer,
    SFUConsumer,
    RoomInfo,
    TransportDirection,
    MediaKind,
)

# Deployment-scoped secret used to derive stable peer / room
# identifiers. Loaded once per process so the same `peer_id`
# reproduces across calls — preventing Janus mediasoup-demo
# reconnections from spinning a brand new room every time.
_STABLE_PEER_SECRET: bytes = b""


def _load_stable_peer_secret() -> bytes:
    """Return the deployment-scoped secret used for stable peer IDs.

    Order of precedence:
    1. ``voice.mediated.stable_peer_secret`` config value (operators
       who want long-lived PIDs across restarts set this),
    2. ``PLEXICHAT_VOICE_STABLE_PEER_SECRET`` env var,
    3. ``PLEXICHAT_SYSTEM_KEY`` env var (the same keyring secret
       used by everything else; reuse keeps cross-module correlation
       deterministic),
    4. ``rate_limiting.bypass_secret`` (also random + stored in keyring),
    5. Final fallback: an empty bytes blob (deterministic but NOT
       secret — emits a single CRITICAL log so operators know).
    """
    global _STABLE_PEER_SECRET
    if _STABLE_PEER_SECRET:
        return _STABLE_PEER_SECRET
    try:
        voice_cfg = _voice_config.get("voice", {}) or {}
        mediated = voice_cfg.get("mediated", {}) or {}
        secret = mediated.get("stable_peer_secret")
        if secret:
            _STABLE_PEER_SECRET = secret.encode("utf-8")
            return _STABLE_PEER_SECRET
    except Exception:
        pass

    for env_name in (
        "PLEXICHAT_VOICE_STABLE_PEER_SECRET",
        "PLEXICHAT_SYSTEM_KEY",
    ):
        env_v = os.environ.get(env_name)
        if env_v:
            _STABLE_PEER_SECRET = env_v.encode("utf-8")
            return _STABLE_PEER_SECRET

    try:
        rl_cfg = _voice_config.get("rate_limiting", {}) or {}
        secret = rl_cfg.get("bypass_secret")
        if secret:
            _STABLE_PEER_SECRET = secret.encode("utf-8")
            return _STABLE_PEER_SECRET
    except Exception:
        pass

    logger.critical(
        "mediasoup_ws: NO deployment-scoped stable-peer secret was "
        "configured. peer_id values will be CONSTANT per instance "
        "(any restart / redeploy rotates them). Set "
        "voice.mediated.stable_peer_secret or "
        "PLEXICHAT_VOICE_STABLE_PEER_SECRET in production."
    )
    return b""


def _stable_peer_id(purpose: bytes) -> str:
    """Compute a stable, deterministic hex-encoded peer/room identifier.

    The output is identical across processes / restarts because we
    mix a deployment-scoped secret with the ``purpose`` payload via
    HMAC-SHA256. Two callers using the same ``purpose`` will get the
    same hex string, so Janus mediasoup reconnections latch onto the
    same room rather than spinning a fresh one every time.

    If the secret is empty (no deployment config), this still returns
    a *deterministic* value — the operator just loses the
    deploy-wide uniqueness guarantee. Operators MUST configure a
    secret before production use.
    """
    secret = _load_stable_peer_secret() or b"plexichat-dev-fallback"
    digest = hmac.new(secret, purpose, hashlib.sha256).hexdigest()
    return digest[:32]


_load_stable_peer_secret()


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


class MediasoupWSAdapter(SFUAdapter):
    """
    WebSocket-based adapter for mediasoup-demo server.

    Uses the protoo protocol for signaling.
    """

    def __init__(
        self,
        ws_url: str,
        timeout: int = 10,
        origin: str = "https://localhost:4443",
    ):
        """
        Initialize the mediasoup WebSocket adapter.

        Args:
            ws_url: WebSocket URL of the mediasoup server (e.g., wss://host:4443)
            timeout: Request timeout in seconds
            origin: Origin header to send (must match server config)
        """
        self._ws_url = ws_url.rstrip("/")
        self._timeout = timeout
        self._origin = origin
        self._connections: Dict[str, PeerConnection] = {}  # peer_id -> connection
        self._rooms: Dict[str, RoomInfo] = {}  # room_id -> info
        self._message_handlers: Dict[str, Callable] = {}
        self._ssl_context = self._create_ssl_context()

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context for the mediasoup signaling socket.

        SECURITY: the previous implementation set
        ``verify_mode=ssl.CERT_NONE`` and ``check_hostname=False`` which
        disabled every protection against an active MITM. Any peer on
        the wire path could substitute / replay packets and our
        WebRTC handshake (carrying ICE / DTLS fingerprints) would still
        accept the malicious server.

        We now require full chain + hostname verification by default.
        Operators who actually need self-signed dev certs must opt in
        explicitly via ``voice.mediated.mtls.allow_self_signed`` (only
        honourable in ``MAINTAINER_MODE``/dev); operators who run
        production with a public CA MUST NOT touch that flag.
        """
        import os as _os
        import utils.config as _cfg

        ctx = ssl.create_default_context()
        try:
            voice_cfg = _cfg.get("voice", {}) or {}
            mediated = voice_cfg.get("mediated", {}) or {}
            mtls_cfg = mediated.get("mtls", {}) or {}
            allow_self_signed = bool(mtls_cfg.get("allow_self_signed", False))
        except Exception:
            allow_self_signed = False

        # Refuse self-signed overrides outside an explicit dev mode.
        # ``PLEXICHAT_ALLOW_INSECURE_TLS=1`` is the documented opt-in
        # but is intentionally noisy: any operator running it in
        # production sees the loud CRITICAL log line below.
        env_override = _os.environ.get("PLEXICHAT_ALLOW_INSECURE_TLS", "").lower() in {
            "1",
            "true",
            "yes",
        }
        env_override = env_override and not _os.environ.get(
            "PLEXICHAT_REQUIRE_FAIL_CLOSED", ""
        )

        if allow_self_signed and env_override:
            logger.critical(
                "mediasoup WebSocket TLS verification DISABLED via "
                "PLEXICHAT_ALLOW_INSECURE_TLS + voice.mediated.mtls."
                "allow_self_signed. This MUST NOT be used in "
                "production."
            )
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def _get_connection_key(self, room_id: str, peer_id: str) -> str:
        """Get unique key for a peer connection."""
        return f"{room_id}:{peer_id}"

    async def _connect(self, room_id: str, peer_id: str) -> PeerConnection:
        """
        Establish WebSocket connection to mediasoup server.

        Args:
            room_id: Room to join
            peer_id: Peer identifier

        Returns:
            PeerConnection instance
        """
        try:
            import websockets
        except ImportError:
            raise SFUConnectionError(
                "websockets library is required for mediasoup WebSocket adapter",
                backend="mediasoup-ws",
                url=self._ws_url,
            )

        key = self._get_connection_key(room_id, peer_id)
        if key in self._connections:
            return self._connections[key]

        # Build WebSocket URL with query parameters
        ws_url = f"{self._ws_url}/?roomId={room_id}&peerId={peer_id}"

        try:
            websocket = await asyncio.wait_for(
                websockets.connect(
                    ws_url,
                    ssl=self._ssl_context,
                    origin=cast(Any, self._origin),
                    subprotocols=cast(Any, ["protoo"]),
                    max_size=960000,
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise SFUTimeoutError(
                "WebSocket connection timed out",
                operation="connect",
                timeout_ms=self._timeout * 1000,
            )
        except Exception as e:
            raise SFUConnectionError(
                f"WebSocket connection failed: {e}", backend="mediasoup-ws", url=ws_url
            )

        connection = PeerConnection(
            peer_id=peer_id,
            room_id=room_id,
            websocket=websocket,
        )
        self._connections[key] = connection

        # Start message receiver task
        asyncio.create_task(self._receive_messages(connection))

        logger.debug(f"Connected to mediasoup: room={room_id}, peer={peer_id}")
        return connection

    async def _receive_messages(self, connection: PeerConnection):
        """Receive and handle messages from WebSocket."""
        try:
            async for message in connection.websocket:
                await self._handle_message(connection, message)
        except Exception as e:
            logger.warning(f"WebSocket receive error: {e}")
        finally:
            key = self._get_connection_key(connection.room_id, connection.peer_id)
            self._connections.pop(key, None)

    async def _handle_message(self, connection: PeerConnection, message: str):
        """Handle incoming protoo message."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {message[:100]}")
            return

        if data.get("response"):
            # Response to a request
            request_id = data.get("id")
            if request_id in connection.pending_requests:
                pending = connection.pending_requests.pop(request_id)
                if data.get("ok"):
                    pending.future.set_result(data.get("data", {}))
                else:
                    error_msg = data.get("errorReason", "Unknown error")
                    pending.future.set_exception(
                        SFUConnectionError(
                            error_msg, backend="mediasoup-ws", url=self._ws_url
                        )
                    )
        elif data.get("request"):
            # Server request (e.g., newConsumer)
            await self._handle_server_request(connection, data)
        elif data.get("notification"):
            # Server notification
            await self._handle_notification(connection, data)

    async def _handle_server_request(self, connection: PeerConnection, data: Dict):
        """Handle server-initiated request."""
        method = data.get("method")
        request_id = data.get("id")
        request_data = data.get("data", {})

        logger.debug(f"Server request: {method}")

        response = {"response": True, "id": request_id, "ok": True, "data": {}}

        if method == "newConsumer":
            # Server wants us to consume a producer
            consumer = SFUConsumer(
                id=request_data.get("id"),
                producer_id=request_data.get("producerId"),
                kind=MediaKind(request_data.get("kind")),
                rtp_parameters=request_data.get("rtpParameters", {}),
                paused=request_data.get("producerPaused", False),
            )
            connection.consumers[consumer.id] = consumer

            # Store consumer info for the handler
            handler_key = f"newConsumer:{connection.room_id}:{connection.peer_id}"
            if handler_key in self._message_handlers:
                await self._message_handlers[handler_key](consumer)

        elif method == "newDataConsumer":
            # Data channel consumer - acknowledge but don't process
            logger.debug(
                f"Acknowledged newDataConsumer from server for peer {connection.peer_id}"
            )
            # No logic needed for data consumers in this implementation

        # Send response
        await connection.websocket.send(json.dumps(response))

    async def _handle_notification(self, connection: PeerConnection, data: Dict):
        """Handle server notification."""
        method = data.get("method")
        notification_data = data.get("data", {})

        logger.debug(f"Server notification: {method}")

        if method == "newPeer":
            # New peer joined
            logger.debug(
                f"New peer joined room {connection.room_id}: {notification_data.get('id')}"
            )
        elif method == "peerClosed":
            # Peer left
            logger.debug(
                f"Peer left room {connection.room_id}: {notification_data.get('peerId')}"
            )
        elif method == "producerScore":
            # Producer quality score
            logger.debug(f"Producer score update: {notification_data}")
        elif method == "consumerScore":
            # Consumer quality score
            logger.debug(f"Consumer score update: {notification_data}")
        elif method == "activeSpeaker":
            # Active speaker changed
            handler_key = f"activeSpeaker:{connection.room_id}"
            if handler_key in self._message_handlers:
                await self._message_handlers[handler_key](notification_data)
        else:
            logger.debug(f"Unhandled notification method: {method}")

    async def _request(
        self,
        connection: PeerConnection,
        method: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Send a protoo request and wait for response.

        Args:
            connection: Peer connection
            method: Request method
            data: Request data

        Returns:
            Response data
        """
        connection.request_id += 1
        request_id = connection.request_id

        request = {
            "request": True,
            "id": request_id,
            "method": method,
            "data": data or {},
        }

        loop = asyncio.get_event_loop()
        future = loop.create_future()

        pending = PendingRequest(
            id=request_id,
            method=method,
            future=future,
            created_at=loop.time(),
        )
        connection.pending_requests[request_id] = pending

        try:
            await connection.websocket.send(json.dumps(request))
            result = await asyncio.wait_for(future, timeout=self._timeout)
            return result
        except asyncio.TimeoutError:
            connection.pending_requests.pop(request_id, None)
            raise SFUTimeoutError(
                f"Request '{method}' timed out",
                operation=method,
                timeout_ms=self._timeout * 1000,
            )
        except Exception as e:
            connection.pending_requests.pop(request_id, None)
            if isinstance(e, (SFUConnectionError, SFUTimeoutError)):
                raise
            raise SFUConnectionError(
                f"Request '{method}' failed: {e}",
                backend="mediasoup-ws",
                url=self._ws_url,
            )

    async def create_room(self, room_id: str) -> RoomInfo:
        """Create a new room (rooms are created on-demand in mediasoup-demo)."""
        if room_id not in self._rooms:
            self._rooms[room_id] = RoomInfo(id=room_id)
        return self._rooms[room_id]

    async def close_room(self, room_id: str) -> bool:
        """Close a room by disconnecting all peers."""
        # Close all connections in this room
        keys_to_remove = [
            key for key in self._connections if key.startswith(f"{room_id}:")
        ]
        for key in keys_to_remove:
            conn = self._connections.pop(key, None)
            if conn and conn.websocket:
                await conn.websocket.close()

        self._rooms.pop(room_id, None)
        return True

    async def join_room(self, room_id: str, peer_id: str) -> Dict[str, Any]:
        """Join a peer to a room."""
        connection = await self._connect(room_id, peer_id)

        # Get router RTP capabilities
        capabilities = await self._request(connection, "getRouterRtpCapabilities")

        # Update room info
        if room_id not in self._rooms:
            self._rooms[room_id] = RoomInfo(id=room_id)
        if peer_id not in self._rooms[room_id].peers:
            self._rooms[room_id].peers.append(peer_id)

        return {
            "routerRtpCapabilities": capabilities,
            "peers": self._rooms[room_id].peers,
            "producers": self._rooms[room_id].producers,
        }

    async def complete_join(
        self,
        room_id: str,
        peer_id: str,
        rtp_capabilities: Dict[str, Any],
        display_name: Optional[str] = None,
    ) -> Any:
        """
        Complete the join process by sending RTP capabilities.

        This must be called after join_room and before producing/consuming.
        """
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            raise SFUConnectionError(
                "Not connected to room", backend="mediasoup-ws", url=self._ws_url
            )

        connection.rtp_capabilities = rtp_capabilities

        result = await self._request(
            connection,
            "join",
            {
                "displayName": display_name or "User",
                "device": {"name": "Plexichat", "version": "1.0"},
                "rtpCapabilities": rtp_capabilities,
                "sctpCapabilities": None,
            },
        )

        connection.joined = True
        return result

    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room."""
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.pop(key, None)

        if connection and connection.websocket:
            await connection.websocket.close()

        if room_id in self._rooms and peer_id in self._rooms[room_id].peers:
            self._rooms[room_id].peers.remove(peer_id)

        return True

    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """Create a WebRTC transport for a peer."""
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            raise SFUConnectionError(
                "Not connected to room", backend="mediasoup-ws", url=self._ws_url
            )

        # mediasoup-demo expects direction to be "producer" or "consumer" inside appData
        producing = direction in (TransportDirection.SEND, TransportDirection.SENDRECV)
        consuming = direction in (TransportDirection.RECV,)

        # Direction string for appData - mediasoup-demo uses "producer" or "consumer"
        dir_str = "producer" if producing else "consumer"

        result = await self._request(
            connection,
            "createWebRtcTransport",
            {
                "forceTcp": False,
                "producing": producing,
                "consuming": consuming,
                "sctpCapabilities": None,
                "appData": {"direction": dir_str},
            },
        )

        # mediasoup-demo returns transportId, not id
        transport_id = str(result.get("transportId") or result.get("id") or "")

        transport = SFUTransport(
            id=transport_id,
            direction=direction,
            ice_parameters=result.get("iceParameters", {}),
            ice_candidates=result.get("iceCandidates", []),
            dtls_parameters=result.get("dtlsParameters", {}),
            sctp_parameters=result.get("sctpParameters"),
        )

        # Store transport ID
        if producing:
            connection.send_transport_id = transport.id
        if consuming:
            connection.recv_transport_id = transport.id

        logger.debug(f"Created transport {transport.id} for peer {peer_id}")
        return transport

    async def connect_transport(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        dtls_parameters: Dict[str, Any],
    ) -> bool:
        """Connect a transport with DTLS parameters."""
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            raise SFUConnectionError(
                "Not connected to room", backend="mediasoup-ws", url=self._ws_url
            )

        await self._request(
            connection,
            "connectWebRtcTransport",
            {
                "transportId": transport_id,
                "dtlsParameters": dtls_parameters,
            },
        )

        logger.debug(f"Connected transport {transport_id}")
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
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            raise SFUConnectionError(
                "Not connected to room", backend="mediasoup-ws", url=self._ws_url
            )

        result = await self._request(
            connection,
            "produce",
            {
                "transportId": transport_id,
                "kind": kind.value,
                "rtpParameters": rtp_parameters,
                "appData": {},
            },
        )

        producer = SFUProducer(
            id=result["id"],
            kind=kind,
            rtp_parameters=rtp_parameters,
            paused=False,
        )

        connection.producers[producer.id] = producer

        # Update room info
        if room_id in self._rooms:
            self._rooms[room_id].producers.append(producer.id)

        logger.debug(f"Created producer {producer.id} ({kind.value})")
        return producer

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

        Note: In mediasoup-demo, consumers are created server-side via newConsumer request.
        This method is for compatibility but consumers are typically created automatically.
        """
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            raise SFUConnectionError(
                "Not connected to room", backend="mediasoup-ws", url=self._ws_url
            )

        # Check if we already have this consumer
        for consumer in connection.consumers.values():
            if consumer.producer_id == producer_id:
                return consumer

        # In mediasoup-demo, consumers are created automatically when joining
        # This is a fallback that shouldn't normally be needed
        raise SFUConnectionError(
            "Consumer not found - consumers are created automatically in mediasoup-demo",
            backend="mediasoup-ws",
            url=self._ws_url,
        )

    async def pause_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Pause a producer."""
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            return False

        await self._request(
            connection,
            "pauseProducer",
            {
                "producerId": producer_id,
            },
        )

        if producer_id in connection.producers:
            connection.producers[producer_id].paused = True

        return True

    async def resume_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Resume a producer."""
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            return False

        await self._request(
            connection,
            "resumeProducer",
            {
                "producerId": producer_id,
            },
        )

        if producer_id in connection.producers:
            connection.producers[producer_id].paused = False

        return True

    async def close_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Close a producer."""
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            return False

        await self._request(
            connection,
            "closeProducer",
            {
                "producerId": producer_id,
            },
        )

        connection.producers.pop(producer_id, None)

        if room_id in self._rooms and producer_id in self._rooms[room_id].producers:
            self._rooms[room_id].producers.remove(producer_id)

        return True

    async def get_room_info(self, room_id: str) -> Optional[RoomInfo]:
        """Get information about a room."""
        return self._rooms.get(room_id)

    async def get_router_capabilities(self, room_id: str) -> Dict[str, Any]:
        """Get RTP capabilities for a room's router."""
        # Find any connection in this room
        for key, connection in self._connections.items():
            if key.startswith(f"{room_id}:"):
                return await self._request(connection, "getRouterRtpCapabilities")

        return {}

    async def set_preferred_layers(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
        spatial_layer: int,
        temporal_layer: int,
    ) -> bool:
        """Set preferred simulcast layers for a consumer."""
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            return False

        await self._request(
            connection,
            "setConsumerPreferredLayers",
            {
                "consumerId": consumer_id,
                "spatialLayer": spatial_layer,
                "temporalLayer": temporal_layer,
            },
        )

        return True

    async def health_check(self) -> bool:
        """Check if the mediasoup server is healthy by attempting a connection."""
        # CORRECTNESS FIX: ``secrets.token_hex(4)`` was being used to
        # generate ``test_room``/``test_peer`` — random strings that
        # change on every call, so each health-check opened a brand
        # new room in mediasoup-demo and the operator had no way to
        # correlate results across runs. We now derive BOTH values
        # deterministically from a deployment-scoped secret so the
        # same physical SFU gets the same probe identity, and falls
        # back to a constant when the secret is missing (dev mode).
        static_purpose = b"plexichat.mediasoup_ws.health_check"
        test_room = _stable_peer_id(static_purpose + b".room")
        test_peer = _stable_peer_id(static_purpose + b".peer")

        try:
            connection = await self._connect(test_room, test_peer)
            await self._request(connection, "getRouterRtpCapabilities")
            await self.leave_room(test_room, test_peer)
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    async def close(self) -> None:
        """Close all connections."""
        for key in list(self._connections.keys()):
            connection = self._connections.pop(key, None)
            if connection and connection.websocket:
                try:
                    await connection.websocket.close()
                except Exception:
                    pass

    def register_consumer_handler(
        self,
        room_id: str,
        peer_id: str,
        handler: Callable,
    ):
        """Register a handler for new consumers."""
        key = f"newConsumer:{room_id}:{peer_id}"
        self._message_handlers[key] = handler

    def register_active_speaker_handler(
        self,
        room_id: str,
        handler: Callable,
    ):
        """Register a handler for active speaker changes."""
        key = f"activeSpeaker:{room_id}"
        self._message_handlers[key] = handler
