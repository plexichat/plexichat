"""
Gateway endpoint - WebSocket endpoint handler.
"""

from typing import Optional
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import utils.logger as logger

from .opcodes import GatewayOpcode, GatewayCloseCode, get_close_message
from .connection import Connection, ConnectionState
from .handlers import OpcodeHandler
from .compression import is_compressed, decompress_payload, validate_message_size, CompressionError


router = APIRouter()


def _get_modules():
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
async def gateway_endpoint(websocket: WebSocket):
    """
    WebSocket gateway endpoint.

    Handles the full connection lifecycle:
    1. Accept connection
    2. Send HELLO with heartbeat interval
    3. Wait for IDENTIFY or RESUME
    4. Handle messages until disconnect
    """
    await websocket.accept()

    session_manager, dispatcher, auth_module, presence_module, servers_module = _get_modules()

    connection_id = session_manager.generate_connection_id()
    connection = Connection(
        websocket=websocket,
        connection_id=connection_id,
        heartbeat_interval_ms=session_manager.heartbeat_interval_ms,
    )

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

        heartbeat_task = asyncio.create_task(
            _heartbeat_monitor(connection, dispatcher)
        )

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
        connection.set_disconnected()
        session_manager.remove_connection(connection_id)
        logger.debug(f"Connection {connection_id} cleaned up")


async def _message_loop(
    connection: Connection,
    handler: OpcodeHandler,
    dispatcher,
) -> None:
    """Handle incoming messages."""
    while connection.state not in (ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED):
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

        data = None
        if "text" in message:
            # Validate text message size
            text_data = message["text"]
            if not validate_message_size(text_data.encode("utf-8")):
                logger.warning(f"Connection {connection.connection_id}: message too large")
                await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                return
            try:
                data = json.loads(text_data)
            except json.JSONDecodeError:
                await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
                return
        elif "bytes" in message:
            raw_bytes = message["bytes"]
            # Validate compressed message size before decompression
            if not validate_message_size(raw_bytes):
                logger.warning(f"Connection {connection.connection_id}: compressed message too large")
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

        opcode = data.get("op")
        payload = data.get("d")

        if opcode is None:
            await _close_connection(connection, GatewayCloseCode.DECODE_ERROR)
            return

        response_op, response_data, close_code = await handler.handle(
            connection, opcode, payload
        )

        if close_code is not None:
            await _close_connection(connection, close_code)
            return

        if response_op is not None:
            if response_op == GatewayOpcode.DISPATCH and response_data:
                event_type = response_data.get("t")
                event_data = response_data.get("d")
                seq = response_data.get("s")

                await connection.send_json({
                    "op": int(GatewayOpcode.DISPATCH),
                    "t": event_type,
                    "s": seq,
                    "d": event_data,
                })

                if event_type == "RESUMED":
                    replay_seq = payload.get("seq", 0) if payload else 0
                    await dispatcher.replay_events(connection, replay_seq)
            elif response_op == GatewayOpcode.HEARTBEAT_ACK:
                await connection.send_json({"op": int(GatewayOpcode.HEARTBEAT_ACK)})
            elif response_op == GatewayOpcode.INVALID_SESSION:
                resumable = response_data.get("d", False) if response_data else False
                await connection.send_json({
                    "op": int(GatewayOpcode.INVALID_SESSION),
                    "d": resumable,
                })
            else:
                await connection.send_json({
                    "op": int(response_op),
                    "d": response_data,
                })


async def _heartbeat_monitor(connection: Connection, dispatcher) -> None:
    """Monitor heartbeat and disconnect if missed."""
    interval = connection.heartbeat_interval_ms / 1000

    while connection.state not in (ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED):
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
        await connection.websocket.close(code=close_code, reason=get_close_message(close_code))
    except Exception:
        pass
    connection.set_disconnected()
