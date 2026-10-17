"""Regression coverage for mediasoup WebSocket receive-path robustness.

``_receive_messages`` iterates the signaling socket and hands each frame to
``_handle_message``. Anything that escapes that handler breaks the ``async
for``, which logs a receive error, drops the peer's connection and ends the
media session -- so every inbound branch must be fault-tolerant, and a server
*request* must always be answered (protoo servers wait on the response).

These tests pin: malformed / non-object frames, duplicate responses, an
always-answered server request, in-flight request failure on disconnect, the
retained receiver-task reference, and probe-connection cleanup.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.core.voice.signaling.exceptions import SFUConnectionError
from src.core.voice.signaling.sfu.mediasoup_ws import (
    MediasoupWSAdapter,
    PeerConnection,
    PendingRequest,
)


def _adapter() -> MediasoupWSAdapter:
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._ws_url = "wss://sfu.example"
    adapter._timeout = 1
    adapter._connections = {}
    adapter._receiver_tasks = {}
    return adapter


def _connection(websocket=None) -> PeerConnection:
    return PeerConnection(
        peer_id="user_456",
        room_id="voice_123",
        websocket=websocket or MagicMock(),
    )


# === Malformed frames ======================================================


@pytest.mark.asyncio
async def test_non_object_json_is_ignored():
    """A JSON array/string must not raise AttributeError out of the loop."""
    adapter = _adapter()
    for payload in ("[1, 2, 3]", '"just a string"', "42", "null"):
        await adapter._handle_message(_connection(), payload)


@pytest.mark.asyncio
async def test_invalid_json_is_ignored():
    await _adapter()._handle_message(_connection(), "{not json")


# === Responses =============================================================


@pytest.mark.asyncio
async def test_duplicate_response_does_not_raise():
    """A second response for a settled future must be dropped, not crash."""
    adapter = _adapter()
    connection = _connection()
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    future.set_result({"first": True})
    connection.pending_requests[7] = PendingRequest(
        id=7, method="join", future=future, created_at=0.0
    )

    await adapter._handle_message(
        connection, json.dumps({"response": True, "id": 7, "ok": True, "data": {}})
    )

    assert future.result() == {"first": True}


@pytest.mark.asyncio
async def test_unknown_response_id_is_ignored():
    await _adapter()._handle_message(
        _connection(), json.dumps({"response": True, "id": 99, "ok": True})
    )


@pytest.mark.asyncio
async def test_ok_response_resolves_pending_request():
    adapter = _adapter()
    connection = _connection()
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    connection.pending_requests[1] = PendingRequest(
        id=1, method="join", future=future, created_at=0.0
    )

    await adapter._handle_message(
        connection,
        json.dumps({"response": True, "id": 1, "ok": True, "data": {"x": 1}}),
    )

    assert future.result() == {"x": 1}
    assert connection.pending_requests == {}


# === Server requests are always answered ===================================


@pytest.mark.asyncio
async def test_failing_consumer_handler_still_answers_the_request():
    """A protoo server waits on the response; never leave it hanging."""
    adapter = _adapter()
    websocket = MagicMock()
    websocket.send = AsyncMock()
    connection = _connection(websocket)

    async def _boom(consumer):
        raise RuntimeError("handler blew up")

    key = f"newConsumer:{connection.room_id}:{connection.peer_id}"
    adapter._message_handlers = {key: _boom}

    await adapter._handle_server_request(
        connection,
        {
            "request": True,
            "id": 5,
            "method": "newConsumer",
            "data": {
                "id": "consumer-1",
                "producerId": "producer-1",
                "kind": "audio",
                "rtpParameters": {},
            },
        },
    )

    websocket.send.assert_awaited_once()
    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["ok"] is False
    assert payload["id"] == 5
    assert "errorReason" in payload


@pytest.mark.asyncio
async def test_successful_consumer_request_is_answered_ok():
    adapter = _adapter()
    websocket = MagicMock()
    websocket.send = AsyncMock()
    connection = _connection(websocket)
    adapter._message_handlers = {}

    await adapter._handle_server_request(
        connection,
        {
            "request": True,
            "id": 6,
            "method": "newConsumer",
            "data": {
                "id": "consumer-2",
                "producerId": "producer-2",
                "kind": "audio",
                "rtpParameters": {},
            },
        },
    )

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["ok"] is True
    assert "consumer-2" in connection.consumers


@pytest.mark.asyncio
async def test_invalid_consumer_kind_is_answered_with_failure():
    """An unknown media kind must not leave the request unanswered."""
    adapter = _adapter()
    websocket = MagicMock()
    websocket.send = AsyncMock()
    connection = _connection(websocket)
    adapter._message_handlers = {}

    await adapter._handle_server_request(
        connection,
        {
            "request": True,
            "id": 7,
            "method": "newConsumer",
            "data": {"id": "c", "producerId": "p", "kind": "smell-o-vision"},
        },
    )

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["ok"] is False


# === In-flight requests on disconnect ======================================


@pytest.mark.asyncio
async def test_disconnect_fails_in_flight_requests():
    adapter = _adapter()
    connection = _connection()
    loop = asyncio.get_running_loop()
    done_future: asyncio.Future = loop.create_future()
    done_future.set_result({})
    live_future: asyncio.Future = loop.create_future()
    connection.pending_requests = {
        1: PendingRequest(id=1, method="a", future=done_future, created_at=0.0),
        2: PendingRequest(id=2, method="b", future=live_future, created_at=0.0),
    }

    adapter._fail_pending_requests(connection)

    assert connection.pending_requests == {}
    assert done_future.result() == {}
    with pytest.raises(SFUConnectionError):
        live_future.result()


@pytest.mark.asyncio
async def test_receive_loop_fails_pending_requests_on_close():
    """When the socket iteration ends, waiters must not hang to timeout."""
    adapter = _adapter()

    class _Closes:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    connection = _connection(_Closes())
    loop = asyncio.get_running_loop()
    pending: asyncio.Future = loop.create_future()
    connection.pending_requests[1] = PendingRequest(
        id=1, method="join", future=pending, created_at=0.0
    )
    adapter._connections[adapter._get_connection_key("voice_123", "user_456")] = (
        connection
    )

    await adapter._receive_messages(connection)

    assert pending.done()
    with pytest.raises(SFUConnectionError):
        pending.result()


# === Task lifetime and probe cleanup =======================================


@pytest.mark.asyncio
async def test_close_cancels_retained_receiver_tasks():
    adapter = _adapter()
    adapter._recordings = {}
    adapter._rooms = {}
    adapter._message_handlers = {}
    task = MagicMock()
    task.done.return_value = False
    adapter._receiver_tasks = {"voice_1:user_1": task}

    await adapter.close()

    task.cancel.assert_called_once()
    assert adapter._receiver_tasks == {}


@pytest.mark.asyncio
async def test_health_check_releases_probe_connection_on_failure(monkeypatch):
    adapter = _adapter()
    leave = AsyncMock(return_value=True)
    monkeypatch.setattr(adapter, "_connect", AsyncMock(return_value=Mock()))
    monkeypatch.setattr(adapter, "_request", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(adapter, "leave_room", leave)

    assert await adapter.health_check() is False
    leave.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_releases_probe_connection_on_success(monkeypatch):
    adapter = _adapter()
    leave = AsyncMock(return_value=True)
    monkeypatch.setattr(adapter, "_connect", AsyncMock(return_value=Mock()))
    monkeypatch.setattr(adapter, "_request", AsyncMock(return_value={}))
    monkeypatch.setattr(adapter, "leave_room", leave)

    assert await adapter.health_check() is True
    assert leave.await_count == 1
