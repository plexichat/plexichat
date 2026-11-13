"""Signaling connection and protoo message dispatch for mediasoup WebSocket."""

import asyncio
import json
from typing import Any, Dict, Optional, cast

import utils.logger as logger

from ..base import MediaKind, SFUConsumer
from ...exceptions import SFUConnectionError, SFUTimeoutError
from .core import MediasoupWSAdapterCore
from .models import PeerConnection, PendingRequest


class ConnectionMixin(MediasoupWSAdapterCore):
    """Connect, receive, and resolve protoo requests and notifications."""

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
        # Encode query values so identifiers cannot alter the signaling URL.
        from urllib.parse import urlencode

        ws_url = f"{self._ws_url}/?{urlencode({'roomId': room_id, 'peerId': peer_id})}"

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

        # Start message receiver task (keep a strong reference; see
        # ``_receiver_tasks``).
        self._receiver_tasks[key] = asyncio.create_task(
            self._receive_messages(connection)
        )

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
            self._receiver_tasks.pop(key, None)
            self._fail_pending_requests(connection)

    @staticmethod
    def _fail_pending_requests(connection: PeerConnection) -> None:
        """Fail every in-flight request when the signaling socket ends.

        Otherwise callers block for the full request timeout after a
        disconnect instead of failing immediately.
        """
        pending = list(connection.pending_requests.values())
        connection.pending_requests.clear()
        for request in pending:
            if request.future.done():
                continue
            request.future.set_exception(
                SFUConnectionError(
                    "Signaling connection closed",
                    backend="mediasoup-ws",
                )
            )

    async def _handle_message(self, connection: PeerConnection, message: str):
        """Handle incoming protoo message."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {message[:100]}")
            return
        if not isinstance(data, dict):
            # A JSON array/string/number is schema-invalid protoo traffic;
            # ``data.get`` would raise AttributeError and end the session.
            logger.warning(f"Ignoring non-object SFU message: {str(data)[:100]}")
            return

        if data.get("response"):
            # Response to a request. Resolving an already-settled future (a
            # duplicate or late response) raises InvalidStateError, which
            # would otherwise escape and tear down the receive loop.
            try:
                raw_id = data.get("id")
                if raw_id is None:
                    return
                # Pending requests are keyed by the integer ids we sent; a
                # non-numeric echo is schema drift and is ignored below.
                request_id = int(raw_id)
                pending = connection.pending_requests.pop(request_id, None)
                if pending is None:
                    return
                if pending.future.done():
                    return
                if data.get("ok"):
                    pending.future.set_result(data.get("data", {}))
                else:
                    error_msg = data.get("errorReason", "Unknown error")
                    pending.future.set_exception(
                        SFUConnectionError(
                            error_msg, backend="mediasoup-ws", url=self._ws_url
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to resolve SFU response: {e}")
        elif data.get("request"):
            # Server request (e.g., newConsumer)
            try:
                await self._handle_server_request(connection, data)
            except Exception as e:
                # One malformed frame must not tear down the peer's SFU
                # session: an escaping exception ends the receive loop in
                # ``_receive_messages`` and drops the connection.
                logger.warning(f"Failed to handle SFU server request: {e}")
        elif data.get("notification"):
            # Server notification
            try:
                await self._handle_notification(connection, data)
            except Exception as e:
                logger.warning(f"Failed to handle SFU notification: {e}")

    async def _handle_server_request(self, connection: PeerConnection, data: Dict):
        """Handle server-initiated request."""
        method = data.get("method")
        request_id = data.get("id")
        request_data = data.get("data", {})

        logger.debug(f"Server request: {method}")

        response: Dict[str, Any] = {
            "response": True,
            "id": request_id,
            "ok": True,
            "data": {},
        }

        try:
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
                    f"Acknowledged newDataConsumer from server for peer "
                    f"{connection.peer_id}"
                )
                # No logic needed for data consumers in this implementation
        except Exception as e:
            # A protoo server WAITS on this response: swallowing the failure
            # without answering leaves the request hanging and can drop the
            # peer. Report the failure in the response instead.
            logger.warning(f"Failed to handle SFU server request {method}: {e}")
            response["ok"] = False
            response["errorReason"] = "request handler failed"

        try:
            await connection.websocket.send(json.dumps(response))
        except Exception as e:
            logger.warning(f"Failed to respond to SFU server request {method}: {e}")

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
            self._report_peer_quality(
                connection,
                notification_data,
                bitrate=notification_data.get("bitrate", 0),
                packet_loss=notification_data.get("packetLoss", 0.0),
                jitter=notification_data.get("jitter", 0.0),
                score=notification_data.get("score", 0),
            )
        elif method == "consumerScore":
            self._report_peer_quality(
                connection,
                notification_data,
                bitrate=notification_data.get("bitrate", 0),
                packet_loss=notification_data.get("packetLoss", 0.0),
                jitter=notification_data.get("jitter", 0.0),
                round_trip_time=notification_data.get("roundTripTime", 0),
                score=notification_data.get("score", 0),
            )
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

        loop = asyncio.get_running_loop()
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
