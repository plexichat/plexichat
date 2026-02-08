"""
Gateway dispatcher - Dispatches events to connected clients.
"""

from typing import Optional, List, Dict, Any, TYPE_CHECKING
import asyncio
import threading

import utils.logger as logger

from src.core.events.models import Event

from .opcodes import GatewayOpcode
from .connection import Connection
from .session import SessionManager
from .intents import filter_event_by_intents

if TYPE_CHECKING:
    from src.core.events.manager import EventManager


class GatewayDispatcher:
    """Dispatches events to connected WebSocket clients."""

    def __init__(
        self,
        session_manager: SessionManager,
        events_module: Optional["EventManager"] = None,
        rate_limit_per_minute: int = 120,
    ):
        """
        Initialize the dispatcher.

        Args:
            session_manager: Session manager instance
            events_module: Events module for subscription
            rate_limit_per_minute: Max events per minute per connection
        """
        self._session_manager = session_manager
        self._events_module: Optional["EventManager"] = events_module
        self._rate_limit_per_minute = rate_limit_per_minute
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the event loop for async dispatch."""
        self._loop = loop

    def on_event(self, event: Event, user_ids: List[int]) -> None:
        """
        Callback for event module subscription.

        Args:
            event: Event to dispatch
            user_ids: Target user IDs
        """
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.dispatch_event(event, user_ids),
                self._loop,
            )
        else:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.dispatch_event(event, user_ids),
                        loop,
                    )
            except RuntimeError:
                pass

    async def dispatch_event(
        self,
        event: Event,
        user_ids: List[int],
    ) -> int:
        """
        Dispatch an event to specified users.

        Args:
            event: Event to dispatch
            user_ids: Target user IDs

        Returns:
            Number of connections event was sent to
        """
        # Ensure user_ids are integers for lookup
        user_ids = [int(uid) for uid in user_ids]

        connections = self._session_manager.get_connections_for_users(user_ids)
        
        # Use DEBUG level for dispatch events to reduce log noise
        logger.debug(
            f"dispatch_event: {event.event_type.value} to {len(user_ids)} users, found {len(connections)} connections"
        )

        if not connections:
            logger.debug(f"No connections found for users: {user_ids[:5]}...")
            return 0

        sent_count = 0
        tasks = []

        for connection in connections:
            if not filter_event_by_intents(event, connection.intents):
                logger.debug(
                    f"Event filtered by intents for connection {getattr(connection, 'id', None)}"
                )
                continue

            if not getattr(
                connection, "is_selftest", False
            ) and not connection.check_rate_limit(self._rate_limit_per_minute):
                logger.debug(
                    f"Rate limited connection {getattr(connection, 'id', None)}"
                )
                continue

            payload = self._build_dispatch_payload(connection, event)
            tasks.append(self._send_to_connection(connection, payload))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            sent_count = sum(1 for r in results if r is True)
            logger.debug(
                f"Dispatched {event.event_type} to {sent_count}/{len(tasks)} connections"
            )

        return sent_count

    async def dispatch_to_connection(
        self,
        connection: Connection,
        event: Event,
    ) -> bool:
        """
        Dispatch an event to a specific connection.

        Args:
            connection: Target connection
            event: Event to dispatch

        Returns:
            True if sent successfully
        """
        if not filter_event_by_intents(event, connection.intents):
            return False

        payload = self._build_dispatch_payload(connection, event)
        return await self._send_to_connection(connection, payload)

    async def dispatch_raw(
        self,
        connection: Connection,
        opcode: GatewayOpcode,
        data: Optional[Dict[str, Any]] = None,
        event_type: Optional[str] = None,
    ) -> bool:
        """
        Dispatch a raw gateway message.

        Args:
            connection: Target connection
            opcode: Gateway opcode
            data: Payload data
            event_type: Event type for DISPATCH opcode

        Returns:
            True if sent successfully
        """
        payload: Dict[str, Any] = {"op": int(opcode)}

        if opcode == GatewayOpcode.DISPATCH:
            payload["s"] = connection.increment_sequence()
            if event_type:
                payload["t"] = event_type
            payload["d"] = data or {}

            if connection.session_id:
                self._session_manager.record_event(connection.session_id, payload)
        else:
            payload["d"] = data

        return await self._send_to_connection(connection, payload)

    async def send_hello(self, connection: Connection) -> bool:
        """
        Send HELLO opcode to a new connection.

        Args:
            connection: Target connection

        Returns:
            True if sent successfully
        """
        return await self.dispatch_raw(
            connection,
            GatewayOpcode.HELLO,
            {"heartbeat_interval": self._session_manager.heartbeat_interval_ms},
        )

    async def send_heartbeat_ack(self, connection: Connection) -> bool:
        """
        Send HEARTBEAT_ACK opcode.

        Args:
            connection: Target connection

        Returns:
            True if sent successfully
        """
        connection.record_heartbeat_ack()
        return await self.dispatch_raw(connection, GatewayOpcode.HEARTBEAT_ACK)

    async def send_invalid_session(
        self,
        connection: Connection,
        resumable: bool = False,
    ) -> bool:
        """
        Send INVALID_SESSION opcode.

        Args:
            connection: Target connection
            resumable: Whether session can be resumed

        Returns:
            True if sent successfully
        """
        return await self.dispatch_raw(
            connection,
            GatewayOpcode.INVALID_SESSION,
            {"resumable": resumable},
        )

    async def send_reconnect(self, connection: Connection) -> bool:
        """
        Send RECONNECT opcode.

        Args:
            connection: Target connection

        Returns:
            True if sent successfully
        """
        return await self.dispatch_raw(connection, GatewayOpcode.RECONNECT)

    async def replay_events(
        self,
        connection: Connection,
        after_sequence: int,
    ) -> int:
        """
        Replay missed events after resume.

        Args:
            connection: Target connection
            after_sequence: Last sequence received by client

        Returns:
            Number of events replayed
        """
        if not connection.session_id:
            return 0

        events = self._session_manager.get_replay_events(
            connection.session_id,
            after_sequence,
        )

        count = 0
        for event_payload in events:
            success = await self._send_to_connection(connection, event_payload)
            if success:
                count += 1

        return count

    def _build_dispatch_payload(
        self,
        connection: Connection,
        event: Event,
    ) -> Dict[str, Any]:
        """Build a DISPATCH payload from an event."""
        seq = connection.increment_sequence()
        payload = {
            "op": int(GatewayOpcode.DISPATCH),
            "t": event.event_type.value,
            "s": seq,
            "d": event.data,
        }

        if connection.session_id:
            self._session_manager.record_event(connection.session_id, payload)

        return payload

    async def _send_to_connection(
        self,
        connection: Connection,
        payload: Dict[str, Any],
    ) -> bool:
        """Send a payload to a connection."""
        try:
            return await connection.send_json(payload)
        except Exception as e:
            logger.debug(
                f"Failed to send to connection {connection.connection_id}: {e}"
            )
            return False

    async def broadcast_to_server(
        self,
        server_id: int,
        event: Event,
        exclude_user_ids: Optional[List[int]] = None,
    ) -> int:
        """
        Broadcast an event to all members of a server.

        Args:
            server_id: Server ID
            event: Event to broadcast
            exclude_user_ids: Users to exclude

        Returns:
            Number of connections event was sent to
        """
        exclude_set: set[int] = set(exclude_user_ids or [])
        sent_count = 0
        tasks: List[tuple[Connection, Dict[str, Any]]] = []

        with self._lock:
            for conn in self._session_manager._connections.values():
                if not conn.is_authenticated:
                    continue
                # conn.user_id is guaranteed to be non-None by is_authenticated
                if conn.user_id is not None and conn.user_id in exclude_set:
                    continue
                if not filter_event_by_intents(event, conn.intents):
                    continue

                payload = self._build_dispatch_payload(conn, event)
                tasks.append((conn, payload))

        for conn, payload in tasks:
            success = await self._send_to_connection(conn, payload)
            if success:
                sent_count += 1

        return sent_count

    async def broadcast_server_status(
        self,
        status_data: Dict[str, Any],
    ) -> int:
        """
        Broadcast server status to all connected clients.

        Used for shutdown/restart notifications.

        Args:
            status_data: Status information containing:
                - state: "shutting_down", "restarting", "maintenance"
                - message: Human-readable message
                - estimated_downtime_seconds: Optional estimated downtime
                - restart_at: Optional restart timestamp

        Returns:
            Number of connections notified
        """
        sent_count = 0

        with self._lock:
            connections = list(self._session_manager._connections.values())

        for conn in connections:
            if conn.is_authenticated:
                payload = {
                    "op": int(GatewayOpcode.SERVER_STATUS),
                    "d": status_data,
                }
                success = await self._send_to_connection(conn, payload)
                if success:
                    sent_count += 1

        logger.info(
            f"Broadcast server status '{status_data.get('state')}' to {sent_count} connections"
        )
        return sent_count

    async def close_all_connections(
        self,
        close_code: int = 4017,
        reason: str = "Server shutting down",
        notify_first: bool = True,
        grace_period_seconds: float = 2.0,
    ) -> int:
        """
        Gracefully close all WebSocket connections.

        Args:
            close_code: WebSocket close code (default: SERVER_SHUTDOWN)
            reason: Close reason message
            notify_first: Whether to send SERVER_STATUS before closing
            grace_period_seconds: Time to wait after notification before closing

        Returns:
            Number of connections closed
        """
        with self._lock:
            connections = list(self._session_manager._connections.values())

        if not connections:
            return 0

        # Notify clients first if requested
        if notify_first:
            await self.broadcast_server_status(
                {
                    "state": "shutting_down",
                    "message": reason,
                    "closing_in_seconds": grace_period_seconds,
                }
            )
            # Give clients time to receive the notification
            await asyncio.sleep(grace_period_seconds)

        closed_count = 0
        for conn in connections:
            try:
                conn.set_disconnecting()
                await conn.websocket.close(code=close_code, reason=reason)
                conn.set_disconnected()
                closed_count += 1
            except Exception as e:
                logger.debug(f"Error closing connection {conn.connection_id}: {e}")

        logger.info(f"Closed {closed_count} WebSocket connections")
        return closed_count
