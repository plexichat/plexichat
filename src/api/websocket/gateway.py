"""
Gateway endpoint - WebSocket endpoint handler.
"""

import asyncio
import json
from typing import Tuple, Optional, Any, Dict, TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import src.api as api
import utils.logger as logger

from .opcodes import GatewayOpcode, GatewayCloseCode, get_close_message
from .connection import Connection, ConnectionState
from .handlers import OpcodeHandler
from .compression import (
    is_compressed,
    decompress_payload,
    validate_message_size,
    CompressionError,
)

if TYPE_CHECKING:
    from .dispatcher import GatewayDispatcher


router = APIRouter()


async def _cleanup_voice_connection(user_id: int) -> None:
    """Clean up voice connection when WebSocket disconnects."""
    try:
        from src.core.voice import signaling

        await signaling.disconnect_voice_async(user_id)
        logger.debug(f"Cleaned up voice connection for user {user_id}")
    except Exception as e:
        logger.debug(f"Voice cleanup for user {user_id}: {e}")


async def _dispatch_offline_presence(
    user_id: int, presence_module: Optional[Any], dispatcher: "GatewayDispatcher"
) -> None:
    """Dispatch offline presence to friends and server members when user disconnects."""
    try:
        import src.api as api

        # Collect all user IDs who should receive this presence update
        target_user_ids = set()

        # Add friends
        relationships = api.get_relationships()
        if relationships:
            try:
                friend_ids = relationships.get_friend_ids(user_id)
                if friend_ids:
                    target_user_ids.update(friend_ids)
            except Exception:
                pass

        # Add server members (users in shared servers) - Optimized single query
        servers = api.get_servers()
        if servers:
            try:
                shared_member_ids = servers.get_all_shared_member_ids(user_id)
                if shared_member_ids:
                    target_user_ids.update(shared_member_ids)
            except Exception as e:
                logger.debug(
                    f"Failed to get shared server members for offline presence: {e}"
                )

        # Clear typing indicators and dispatch TYPING_STOP events
        if presence_module:
            try:
                typing_channels = presence_module.clear_all_typing(user_id)
                if typing_channels:
                    from src.core.events.models import Event
                    from src.core.events.types import EventType

                    # Dispatch TYPING_STOP for each channel in parallel
                    tasks = []
                    for channel_id in typing_channels:
                        event = Event(
                            event_type=EventType.TYPING_STOP,
                            data={
                                "channel_id": str(channel_id),
                                "user_id": str(user_id),
                            },
                            channel_id=channel_id,
                        )
                        # Dispatch to all potential viewers
                        if target_user_ids:
                            tasks.append(
                                dispatcher.dispatch_event(event, list(target_user_ids))
                            )

                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    logger.debug(
                        f"Cleared typing for user {user_id} in {len(typing_channels)} channels"
                    )
            except Exception as e:
                logger.debug(f"Failed to clear typing on disconnect: {e}")

        if not target_user_ids:
            return

        # Update presence to offline
        if presence_module:
            try:
                from src.core.presence.models import UserStatus

                presence_module.set_status(user_id, UserStatus.OFFLINE)
            except Exception:
                pass

        # Dispatch presence update to all relevant users
        from src.core.events.models import Event
        from src.core.events.types import EventType

        event = Event(
            event_type=EventType.PRESENCE_UPDATE,
            data={
                "user_id": str(user_id),
                "status": "offline",
                "custom_status": None,
                "custom_emoji": None,
            },
        )
        await dispatcher.dispatch_event(event, list(target_user_ids))
        logger.debug(
            f"Dispatched offline presence for user {user_id} to {len(target_user_ids)} users"
        )
    except Exception as e:
        logger.debug(f"Failed to dispatch offline presence: {e}")


def _get_modules() -> Tuple[Any, Any, Optional[Any], Optional[Any], Optional[Any]]:
    """Get module references from the websocket package."""
    from . import (
        get_session_manager,
        get_dispatcher,
        get_auth_module,
        get_presence_module,
        get_servers_module,
    )

    return (
        get_session_manager(),
        get_dispatcher(),
        get_auth_module(),
        get_presence_module(),
        get_servers_module(),
    )


@router.websocket("/gateway")
async def gateway_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket gateway endpoint.

    Handles the full connection lifecycle:
    1. Accept connection
    2. Send HELLO with heartbeat interval
    3. Wait for IDENTIFY or RESUME
    4. Handle messages until disconnect
    """
    await websocket.accept()

    # Detect if this is a secure self-test connection
    # Uses centralized validation (is_local + hmac.compare_digest)
    is_selftest = api.is_self_test_request(websocket)

    session_manager, dispatcher, auth_module, presence_module, servers_module = (
        _get_modules()
    )

    connection_id = session_manager.generate_connection_id()
    connection = Connection(
        websocket=websocket,
        connection_id=connection_id,
        heartbeat_interval_ms=session_manager.heartbeat_interval_ms,
    )
    # Store is_selftest on connection for handlers to use
    connection.is_selftest = is_selftest

    session_manager.add_connection(connection)
    connection.state = ConnectionState.CONNECTED

    handler = OpcodeHandler(
        session_manager=session_manager,
        auth_module=auth_module,
        presence_module=presence_module,
        servers_module=servers_module,
    )

    dispatcher.set_event_loop(asyncio.get_event_loop())

    logger.debug(f"New gateway connection: {connection_id}")

    try:
        await dispatcher.send_hello(connection)

        heartbeat_task = asyncio.create_task(_heartbeat_monitor(connection, dispatcher))

        try:
            await _message_loop(connection, handler, dispatcher)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect as e:
        logger.debug(f"Connection {connection_id} disconnected: code={e.code}")
    except Exception as e:
        logger.error(f"Gateway error for {connection_id}: {e}")
    finally:
        # Clean up voice connection when WebSocket disconnects
        if connection.is_authenticated and connection.user_id:
            await _cleanup_voice_connection(connection.user_id)
            await _dispatch_offline_presence(
                connection.user_id, presence_module, dispatcher
            )

        connection.set_disconnected()
        session_manager.remove_connection(connection_id)
        logger.debug(f"Connection {connection_id} cleaned up")


async def _message_loop(
    connection: Connection,
    handler: OpcodeHandler,
    dispatcher: "GatewayDispatcher",
) -> None:
    """Handle incoming messages."""
    while connection.state not in (
        ConnectionState.DISCONNECTING,
        ConnectionState.DISCONNECTED,
    ):
        try:
            message = await asyncio.wait_for(
                connection.websocket.receive(),
                timeout=connection.heartbeat_interval_ms / 1000 * 2,
            )
        except asyncio.TimeoutError:
            if not connection.is_alive:
                await _close_connection(
                    connection,
                    GatewayCloseCode.SESSION_TIMED_OUT,
                )
                return
            continue

        if message["type"] == "websocket.disconnect":
            return

        data: Optional[Dict[str, Any]] = None
        if "text" in message:
            # Validate text message size
            text_data: str = message["text"]
            if not validate_message_size(text_data.encode("utf-8")):
                logger.warning(
                    f"Connection {connection.connection_id}: message too large"
                )
                await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                return
            try:
                data = json.loads(text_data)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Connection {connection.connection_id}: JSON decode error: {e}, data: {text_data[:200]}"
                )
                await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                return
        elif "bytes" in message:
            raw_bytes: bytes = message["bytes"]
            # Validate compressed message size before decompression
            if not validate_message_size(raw_bytes):
                logger.warning(
                    f"Connection {connection.connection_id}: compressed message too large"
                )
                await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                return
            if is_compressed(raw_bytes):
                try:
                    data = decompress_payload(raw_bytes)
                except CompressionError as e:
                    logger.warning(f"Connection {connection.connection_id}: {e}")
                    await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                    return
            else:
                try:
                    data = json.loads(raw_bytes.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                    return

            if data is None:
                await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                return

        if data is None:
            continue

        opcode: Optional[Any] = data.get("op")
        payload: Optional[Any] = data.get("d")

        if opcode is None:
            logger.warning(
                f"Connection {connection.connection_id}: missing opcode in message: {data}"
            )
            await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
            return

        response_op: Optional[int]
        response_data: Optional[Dict[str, Any]]
        close_code: Optional[int]
        response_op, response_data, close_code = await handler.handle(
            connection, opcode, payload
        )

        if close_code is not None:
            await _close_connection(connection, close_code)
            return

        if response_op is not None:
            if response_op == GatewayOpcode.DISPATCH and response_data:
                event_type: Optional[Any] = response_data.get("t")
                event_data: Optional[Any] = response_data.get("d")
                seq: Optional[Any] = response_data.get("s")

                await connection.send_json(
                    {
                        "op": int(GatewayOpcode.DISPATCH),
                        "t": event_type,
                        "s": seq,
                        "d": event_data,
                    }
                )

                if event_type == "RESUMED":
                    replay_seq: int = payload.get("seq", 0) if payload else 0
                    await dispatcher.replay_events(connection, replay_seq)
            elif response_op == GatewayOpcode.HEARTBEAT_ACK:
                await connection.send_json({"op": int(GatewayOpcode.HEARTBEAT_ACK)})
            elif response_op == GatewayOpcode.INVALID_SESSION:
                resumable: bool = (
                    response_data.get("d", False) if response_data else False
                )
                await connection.send_json(
                    {
                        "op": int(GatewayOpcode.INVALID_SESSION),
                        "d": resumable,
                    }
                )
            else:
                await connection.send_json(
                    {
                        "op": int(response_op),
                        "d": response_data,
                    }
                )


async def _heartbeat_monitor(
    connection: Connection, dispatcher: "GatewayDispatcher"
) -> None:
    """Monitor heartbeat and disconnect if missed."""
    interval = connection.heartbeat_interval_ms / 1000

    while connection.state not in (
        ConnectionState.DISCONNECTING,
        ConnectionState.DISCONNECTED,
    ):
        await asyncio.sleep(interval)

        if not connection.is_alive:
            connection.missed_heartbeats += 1
            if connection.missed_heartbeats >= 2:
                logger.debug(
                    f"Connection {connection.connection_id} missed heartbeats, disconnecting"
                )
                await _close_connection(connection, GatewayCloseCode.SESSION_TIMED_OUT)
                return


async def _close_connection(connection: Connection, close_code: int) -> None:
    """Close a connection with a specific code."""
    connection.set_disconnecting()
    try:
        await connection.websocket.close(
            code=close_code, reason=get_close_message(close_code)
        )
    except Exception:
        pass
    connection.set_disconnected()
