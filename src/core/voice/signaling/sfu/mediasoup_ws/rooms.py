"""Room, transport, and producer/consumer operations for mediasoup WebSocket."""

from typing import Any, Dict, Optional

import utils.logger as logger

from ..base import (
    MediaKind,
    RoomInfo,
    SFUConsumer,
    SFUProducer,
    SFUTransport,
    TransportDirection,
)
from ...exceptions import SFUConnectionError
from .core import MediasoupWSAdapterCore
from .identity import _stable_peer_id


class RoomsMixin(MediasoupWSAdapterCore):
    """Rooms, transports, producers, layers, and health checks."""

    async def create_room(self, room_id: str) -> RoomInfo:
        """Create a new room (rooms are created on-demand in mediasoup-demo)."""
        if room_id not in self._rooms:
            self._rooms[room_id] = RoomInfo(id=room_id)
        return self._rooms[room_id]

    async def close_room(self, room_id: str) -> bool:
        """Close a room by disconnecting all peers."""
        # Close any active recording before removing the room state.
        if room_id in self._recordings:
            teardown_error: Optional[Exception] = None
            try:
                await self.stop_recording(room_id)
            except Exception as exc:
                teardown_error = exc
                logger.warning(
                    "Failed to stop mediasoup WebSocket recording for room %s: %s",
                    room_id,
                    exc,
                )
            # Retry any retained state, including state preserved after an
            # unexpected cleanup exception, while the connection is available.
            if room_id in self._recordings:
                try:
                    await self.stop_recording(room_id)
                except Exception as exc:
                    teardown_error = exc
                    logger.warning(
                        "Retry failed for mediasoup WebSocket recording in room %s: %s",
                        room_id,
                        exc,
                    )
            # The room's signaling connection is about to be destroyed. Do not
            # leave metadata claiming that a later retry is possible.
            if room_id in self._recordings:
                reason = "room closed before recorder teardown completed"
                if teardown_error is not None:
                    reason = f"{reason}: {teardown_error}"
                self._record_recording_teardown_failure(room_id, reason)

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
                "acoustic_defense": self._acoustic_defense_to_dict(),
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
        """Set preferred simulcast layers for the peer's consumers.

        ``BandwidthEstimator`` calls this with an empty ``consumer_id`` -- it
        has no per-consumer mapping -- and the previous code sent
        ``consumerId: ""`` verbatim, which mediasoup cannot resolve, so the
        degradation hint silently never applied. An empty id now means "every
        consumer this peer receives"; layers are meaningful for video, so
        video consumers are preferred and audio ones are only used when the
        peer has no video consumer. Returns False when there is nothing to
        configure rather than claiming success.
        """
        key = self._get_connection_key(room_id, peer_id)
        connection = self._connections.get(key)
        if not connection:
            return False

        if consumer_id:
            consumer_ids = [consumer_id]
        else:
            consumer_ids = [
                consumer.id
                for consumer in connection.consumers.values()
                if consumer.kind is MediaKind.VIDEO
            ] or [consumer.id for consumer in connection.consumers.values()]

        if not consumer_ids:
            logger.debug(
                f"No consumers to configure for peer {peer_id} in room {room_id}"
            )
            return False

        # An SFU rejection propagates rather than being swallowed: reporting
        # success on a failed layer change is the fail-open shape this audit
        # exists to remove (see test_mediasoup_simulcast_layer_manipulation).
        for target_id in consumer_ids:
            await self._request(
                connection,
                "setConsumerPreferredLayers",
                {
                    "consumerId": target_id,
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
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False
        finally:
            # Always release the probe connection. Leaving it registered on a
            # failed probe kept the websocket and its receiver task alive, so
            # every periodic health check leaked one connection.
            try:
                await self.leave_room(test_room, test_peer)
            except Exception as exc:
                logger.debug(f"Health check cleanup failed: {exc}")
