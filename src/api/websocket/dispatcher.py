"""
Gateway dispatcher - Dispatches events to connected clients.
"""

from typing import Optional, List, Dict, Any, TYPE_CHECKING
import asyncio
import threading

import utils.logger as logger

from src.core.events.models import Event
from src.core.events.snowflakes import stringify_snowflake_ids

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
        # Observable back-pressure signal (C2): events dropped because a
        # connection exceeded its per-minute send budget. Exposed via
        # :meth:`get_rate_limit_metrics` instead of being silently
        # debug-dropped.
        self._rate_limited_events = 0

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the event loop for async dispatch."""
        self._loop = loop

    def get_rate_limit_metrics(self) -> Dict[str, int]:
        """Expose outbound back-pressure metrics for observability (C2).

        Returns the cumulative number of events dropped because a
        connection exceeded its per-minute rate budget, plus the
        configured budget itself.
        """
        with self._lock:
            return {
                "rate_limited_events": self._rate_limited_events,
                "rate_limit_per_minute": self._rate_limit_per_minute,
            }

    def on_event(self, event: Event, user_ids: List[int]) -> None:
        """
        Callback for event module subscription.

        Args:
            event: Event to dispatch
            user_ids: Target user IDs
        """
        # C7: prefer the current thread's running loop, then the captured
        # per-connection loop. asyncio.get_event_loop() outside a running loop
        # is deprecated on 3.12+ and the previous except-pass silently DROPPED
        # events whenever neither loop was available (e.g. startup ordering
        # before set_event_loop).
        loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop
        if loop is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.dispatch_event(event, user_ids),
                loop,
            )
            return
        logger.warning(
            f"GatewayDispatcher: no running event loop to dispatch "
            f"{event.event_type.value} to {len(user_ids)} users; event dropped"
        )

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
                # BACK-PRESSURE (C2): rate-limited sends are counted and
                # logged at WARNING (not silently debug-dropped) so
                # saturation is observable. The counter is exposed via
                # get_rate_limit_metrics() for /health-style dashboards.
                with self._lock:
                    self._rate_limited_events += 1
                    total_limited = self._rate_limited_events
                logger.warning(
                    f"Rate limited connection {connection.connection_id} "
                    f"(user={connection.user_id}); dropped "
                    f"{event.event_type.value} "
                    f"(total rate-limited drops: {total_limited})"
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

    async def dispatch_remote_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        user_ids: List[int],
    ) -> int:
        """Dispatch an event received from a sibling worker via Valkey pub/sub.

        Reconstructs an :class:`Event` from the serialized fields,
        then fans out to local connections exactly like
        :meth:`dispatch_event`.

        Args:
            event_type: Event type string (e.g. ``"MESSAGE_CREATE"``).
            event_data: Event payload dict.
            user_ids: Target user IDs (already filtered by the
                      publishing worker's EventRouter).

        Returns:
            Number of local connections the event was sent to.
        """
        from src.core.events.types import EventType

        try:
            resolved_type = EventType(event_type)
        except ValueError:
            logger.debug(
                "Ignoring cross-worker event with unknown type: %s", event_type
            )
            return 0

        event = Event(event_type=resolved_type, data=event_data)
        return await self.dispatch_event(event, user_ids)

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
        data: Optional[Any] = None,
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

    async def dispatch_raw_to_users(
        self,
        user_ids: List[int],
        opcode: GatewayOpcode,
        data: Optional[Any] = None,
    ) -> int:
        """Fan a raw (non-DISPATCH) frame out to every connection of ``user_ids``.

        ``dispatch_raw`` targets a single connection; an opcode frame that is
        peer-visible state (a voice speaking indicator) has no single target.
        Mirrors ``dispatch_event``'s connection lookup, rate-limit accounting
        and gather semantics. Intents are not consulted: they gate *events*
        (which carry an event type), and a raw frame carries none.

        Args:
            user_ids: Target user IDs
            opcode: Gateway opcode to send
            data: Payload data

        Returns:
            Number of connections the frame was sent to
        """
        target_ids = [int(uid) for uid in user_ids]
        if not target_ids:
            return 0

        connections = self._session_manager.get_connections_for_users(target_ids)
        if not connections:
            return 0

        tasks = []
        for connection in connections:
            if not getattr(
                connection, "is_selftest", False
            ) and not connection.check_rate_limit(self._rate_limit_per_minute):
                with self._lock:
                    self._rate_limited_events += 1
                logger.warning(
                    f"Rate limited connection {connection.connection_id} "
                    f"(user={connection.user_id}); dropped raw opcode {int(opcode)}"
                )
                continue
            tasks.append(self.dispatch_raw(connection, opcode, data))

        if not tasks:
            return 0

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if r is True)

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
        sent = await self.dispatch_raw(connection, GatewayOpcode.HEARTBEAT_ACK)
        if sent:
            # Record only once the ACK was actually written (Agent 38 F7).
            connection.record_heartbeat_ack()
        return sent

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
            resumable,
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
            # Central backstop: every id that crosses the wire is a decimal
            # string, regardless of how the event was built.
            "d": stringify_snowflake_ids(event.data),
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

    def _resolve_server_member_ids(self, server_id: int) -> set:
        """Resolve the user ids that are members of ``server_id``.

        Returns an empty set when membership cannot be determined (no
        servers module wired, or the lookup failed) so callers fail closed
        instead of over-broadcasting.
        """
        try:
            from src.api.websocket import get_servers_module

            servers_module = get_servers_module()
        except Exception as e:
            logger.warning(
                f"GatewayDispatcher: servers module unavailable while resolving "
                f"members of server {server_id}: {e}"
            )
            return set()
        if not servers_module:
            logger.warning(
                f"GatewayDispatcher: no servers module wired; refusing to "
                f"resolve members of server {server_id}"
            )
            return set()
        try:
            return {int(uid) for uid in servers_module.get_member_user_ids(server_id)}
        except Exception as e:
            logger.warning(
                f"GatewayDispatcher: failed to list members of server {server_id}: {e}"
            )
            return set()

    async def broadcast_to_server(
        self,
        server_id: int,
        event: Event,
        exclude_user_ids: Optional[List[int]] = None,
    ) -> int:
        """
        Broadcast an event to all members of a server.

        SECURITY: this previously fanned out to EVERY connected authenticated
        user regardless of membership, despite its name and docstring, so any
        server-scoped event pushed through it leaked to non-members. Recipients
        are now resolved through the servers module and the broadcast fails
        closed (sends nothing) when membership cannot be determined.

        Args:
            server_id: Server ID
            event: Event to broadcast
            exclude_user_ids: Users to exclude

        Returns:
            Number of connections event was sent to
        """
        exclude_set: set[int] = set(exclude_user_ids or [])
        sent_count = 0
        tasks = []

        member_ids = self._resolve_server_member_ids(server_id)
        target_ids = [uid for uid in member_ids if uid not in exclude_set]
        if not target_ids:
            logger.debug(
                f"GatewayDispatcher: no deliverable members for server "
                f"{server_id}; dropping {event.event_type.value}"
            )
            return 0

        connections = self._session_manager.get_connections_for_users(target_ids)
        for conn in connections:
            if not conn.is_authenticated:
                continue
            # conn.user_id is guaranteed to be non-None by is_authenticated
            if conn.user_id is not None and conn.user_id in exclude_set:
                continue
            if not filter_event_by_intents(event, conn.intents):
                continue

            payload = self._build_dispatch_payload(conn, event)
            tasks.append(self._send_to_connection(conn, payload))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            sent_count = sum(1 for r in results if r is True)

        return sent_count

    async def broadcast_server_status(
        self,
        status_data: Dict[str, Any],
        connections: Optional[List[Connection]] = None,
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
            connections: Optional pre-fetched connection list to avoid double-snapshot

        Returns:
            Number of connections notified
        """
        sent_count = 0
        tasks = []

        if connections is None:
            with self._lock:
                connections = self._session_manager.get_all_connections()

        for conn in connections:
            if conn.is_authenticated:
                payload = {
                    "op": int(GatewayOpcode.SERVER_STATUS),
                    "d": status_data,
                }
                tasks.append(self._send_to_connection(conn, payload))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            sent_count = sum(1 for r in results if r is True)

        logger.info(
            f"Broadcast server status '{status_data.get('state')}' to {sent_count} connections"
        )
        return sent_count

    async def broadcast_ratchet_update(
        self,
        conversation_id: int,
        update_data: Dict[str, Any],
        connections: Optional[List[Connection]] = None,
    ) -> int:
        """
        Broadcast a RATCHET_UPDATE to all members of a conversation.

        Sent whenever the channel ratchet is rotated, split, or
        re-anchored. Connected clients can use this to invalidate
        any cached active-interval key before the next message
        arrives.

        Args:
            conversation_id: Conversation that experienced the ratchet change.
            update_data: Payload describing the change. Expected keys:
                - reason: "rotation" | "split" | "re_anchor" | ...
                - new_interval_id: Optional[int]
                - at_message_id: Optional[int]
                - at: Optional[int] ms timestamp
            connections: Optional pre-fetched connection list.

        Returns:
            Number of connections notified.
        """
        sent_count = 0
        tasks = []

        if connections is None:
            # SECURITY: scope the broadcast to conversation participants only.
            # Sending ratchet rotations (conversation ID, message anchor,
            # timing) to every connected user would leak private-conversation
            # metadata, unlike every other event which is routed via
            # EventRouter.get_recipients.
            try:
                from src.core.messaging import get_participant_ids

                participant_ids = set(get_participant_ids(conversation_id))
                connections = self._session_manager.get_connections_for_users(
                    list(participant_ids)
                )
            except Exception as e:
                logger.warning(
                    f"Failed to resolve participants for RATCHET_UPDATE "
                    f"(conversation {conversation_id}): {e}"
                )
                connections = []

        for conn in connections:
            if not conn.is_authenticated:
                continue
            # F9: ratchet rotations must be sequenced and recorded in the
            # session replay buffer exactly like every other dispatched
            # event. Previously the frame was built once without an ``s``
            # and sent raw, so clients could not dedupe it across
            # reconnects and a rotation that fired while a participant was
            # disconnected was never redelivered on resume -- leaving that
            # client with a stale ratchet interval key.
            payload = {
                "op": int(GatewayOpcode.RATCHET_UPDATE),
                "t": "RATCHET_UPDATE",
                "s": conn.increment_sequence(),
                "d": stringify_snowflake_ids(
                    {
                        "conversation_id": int(conversation_id),
                        **dict(update_data),
                    }
                ),
            }
            if conn.session_id:
                self._session_manager.record_event(conn.session_id, payload)
            tasks.append(self._send_to_connection(conn, payload))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            sent_count = sum(1 for r in results if r is True)

        logger.info(
            f"Broadcast RATCHET_UPDATE for conversation {conversation_id} "
            f"to {sent_count} connections (reason={update_data.get('reason')})"
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
            connections = self._session_manager.get_all_connections()

        if not connections:
            return 0

        # Notify clients first if requested (reuse the same connection snapshot)
        if notify_first:
            await self.broadcast_server_status(
                {
                    "state": "shutting_down",
                    "message": reason,
                    "closing_in_seconds": grace_period_seconds,
                },
                connections=connections,
            )
            # Give clients time to receive the notification
            await asyncio.sleep(grace_period_seconds)

        closed_count = 0
        tasks = []

        async def _close_conn(conn):
            try:
                conn.set_disconnecting()
                await conn.websocket.close(code=close_code, reason=reason)
                conn.set_disconnected()
                return True
            except Exception as e:
                logger.debug(f"Error closing connection {conn.connection_id}: {e}")
                return False

        for conn in connections:
            tasks.append(_close_conn(conn))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            closed_count = sum(1 for r in results if r is True)

        logger.info(f"Closed {closed_count} WebSocket connections")
        return closed_count
