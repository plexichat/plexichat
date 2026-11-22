"""Gateway throughput / back-pressure regression tests.

These tie the coalescing queue, render cache, subscription index and bounded
push fan-out together under burst load. The assertions are structural (frame
counts, batch counts, shed counts) rather than wall-clock timings, so they do
not flake on a busy CI worker.
"""

import asyncio
import threading
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.artifacts import ArtifactSubscriptionRegistry
from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.intents import ALL_INTENTS
from src.api.websocket.session import SessionManager
from src.core.events.models import Event
from src.core.events.types import EventType


class _SessionManager:
    def __init__(self, connections):
        self._connections = connections

    def get_connections_for_users(self, user_ids):
        return list(self._connections)

    def record_event(self, session_id, event):
        pass


class CountingConnection(Connection):
    """Connection that counts coalesced writer invocations."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.write_calls = 0

    async def _write_frames(self, frames):
        self.write_calls += 1
        return await super()._write_frames(frames)


async def _pause_auto_flush(conn):
    """Stop the timer task so the test controls exactly when flushes happen."""
    task = conn._batcher_task
    conn._batcher_task = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _connection(name, *, queue_size=4096, max_batch=64):
    conn = CountingConnection(
        websocket=AsyncMock(), connection_id=name, intents=ALL_INTENTS
    )
    conn.state = ConnectionState.READY
    conn.user_id = 123
    conn.is_selftest = True
    conn.enable_batching(queue_size=queue_size, max_batch=max_batch, flush_interval=1.0)
    return conn


@pytest.mark.asyncio
async def test_burst_fanout_coalesces_writes_without_losing_frames(monkeypatch):
    import src.api.websocket.dispatcher as dispatcher_module

    real_renderer = dispatcher_module.stringify_snowflake_ids
    render_calls = []

    def counting_renderer(data):
        render_calls.append(data)
        return real_renderer(data)

    monkeypatch.setattr(dispatcher_module, "stringify_snowflake_ids", counting_renderer)

    connections = [_connection(f"c{i}") for i in range(200)]
    for conn in connections:
        await _pause_auto_flush(conn)
    dispatcher = GatewayDispatcher(cast(SessionManager, _SessionManager(connections)))

    try:
        for seq in range(20):
            event = Event(
                event_type=EventType.MESSAGE_CREATE,
                data={"message_id": 1000 + seq, "content": "burst"},
            )
            await dispatcher.dispatch_event(event, [123])

        for conn in connections:
            await conn.flush_outbound()
    finally:
        for conn in connections:
            await conn.stop_batching()

    # 200 connections x 20 events = 4000 independent WebSocket frames.
    for conn in connections:
        frames = [
            call.args[0]
            for call in cast(AsyncMock, conn.websocket.send_json).await_args_list
        ]
        assert len(frames) == 20
        assert [frame["s"] for frame in frames] == list(range(1, 21))
        assert conn.write_calls == 1  # 20 frames coalesced into one flush

    assert sum(conn.write_calls for conn in connections) == 200
    # Distinct event payloads are rendered once each, not once per recipient.
    assert len(render_calls) == 20
    assert dispatcher.get_render_cache_stats()["misses"] == 20


@pytest.mark.asyncio
async def test_slow_consumer_queue_is_bounded_and_counts_shedding():
    conn = _connection("slow", queue_size=8, max_batch=4)
    await _pause_auto_flush(conn)
    batcher = conn.outbound_batcher
    assert batcher is not None
    try:
        pending_samples = []
        accepted = 0
        for i in range(100):
            if await conn.send_json({"op": 1, "s": i}):
                accepted += 1
            pending_samples.append(batcher.pending)

        assert accepted == 8
        assert max(pending_samples) == 8
        assert batcher.stats()["dropped_frames"] == 92

        await conn.flush_outbound()
        sent = [
            call.args[0]["s"]
            for call in cast(AsyncMock, conn.websocket.send_json).await_args_list
        ]
        assert sent == list(range(8))  # oldest accepted frames, in order
    finally:
        await conn.stop_batching()


@pytest.mark.asyncio
async def test_one_saturated_connection_does_not_block_other_recipients():
    saturated = _connection("saturated", queue_size=1, max_batch=1)
    healthy = _connection("healthy")
    await _pause_auto_flush(saturated)
    await _pause_auto_flush(healthy)
    dispatcher = GatewayDispatcher(
        cast(SessionManager, _SessionManager([saturated, healthy]))
    )

    try:
        # Saturate one connection before the fan-out.
        assert await saturated.send_json({"op": 99}) is True
        event = Event(
            event_type=EventType.PRESENCE_UPDATE,
            data={"user_id": 5, "status": "online"},
        )
        sent = await dispatcher.dispatch_event(event, [123])

        await saturated.flush_outbound()
        await healthy.flush_outbound()
    finally:
        await saturated.stop_batching()
        await healthy.stop_batching()

    # The saturated connection sheds the frame (backpressure reported as a
    # non-success); the healthy one still receives it.
    assert sent == 1
    assert saturated.outbound_batcher is None
    healthy_frames = [
        call.args[0]
        for call in cast(AsyncMock, healthy.websocket.send_json).await_args_list
    ]
    assert len(healthy_frames) == 1
    assert healthy_frames[0]["d"]["user_id"] == "5"


@pytest.mark.asyncio
async def test_identical_presence_burst_renders_once(monkeypatch):
    import src.api.websocket.dispatcher as dispatcher_module

    real_renderer = dispatcher_module.stringify_snowflake_ids
    render_calls = []

    def counting_renderer(data):
        render_calls.append(data)
        return real_renderer(data)

    monkeypatch.setattr(dispatcher_module, "stringify_snowflake_ids", counting_renderer)

    conn = _connection("presence")
    # Batching is irrelevant to rendering; disable it so every frame is on the
    # wire immediately and the count is deterministic.
    await conn.stop_batching()
    dispatcher = GatewayDispatcher(cast(SessionManager, _SessionManager([conn])))

    for _ in range(500):
        await dispatcher.dispatch_event(
            Event(
                event_type=EventType.PRESENCE_UPDATE,
                data={"user_id": 77, "status": "idle", "custom_status": "afk"},
            ),
            [123],
        )

    assert len(render_calls) == 1
    assert len(cast(AsyncMock, conn.websocket.send_json).await_args_list) == 500


@pytest.mark.asyncio
async def test_duplicate_user_ids_do_not_duplicate_fanout():
    """A repeated user id must not deliver the same frame to one socket twice.

    Direct dispatcher callers bypass ``EventRouter``'s recipient dedupe, so
    ``[123, 123, 123]`` reaches the connection lookup verbatim. Before the
    lookup deduped, that produced three frames, three sequence numbers and
    three serialization/send passes on a single connection.
    """
    from src.api.websocket.session import SessionManager

    # Build without __init__ so the test never touches Valkey.
    session_manager = object.__new__(SessionManager)
    session_manager._connections = {}
    session_manager._user_connections = {}
    session_manager._lock = threading.Lock()

    conn = Connection(websocket=AsyncMock(), connection_id="dup", intents=ALL_INTENTS)
    conn.state = ConnectionState.READY
    conn.user_id = 123
    conn.is_selftest = True
    session_manager._connections[conn.connection_id] = conn
    session_manager._user_connections[123] = {conn.connection_id}

    # Dedupe at the lookup boundary, preserving first-seen order.
    assert session_manager.get_connections_for_users([123, 123, 123]) == [conn]

    dispatcher = GatewayDispatcher(cast(SessionManager, session_manager))
    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={"message_id": 1, "content": "once"},
    )
    sent = await dispatcher.dispatch_event(event, [123, 123, 123])

    assert sent == 1
    assert conn.sequence == 1
    frames = [
        call.args[0]
        for call in cast(AsyncMock, conn.websocket.send_json).await_args_list
    ]
    assert len(frames) == 1
    assert frames[0]["s"] == 1


@pytest.mark.asyncio
async def test_subscription_teardown_stays_indexed_at_scale():
    registry = ArtifactSubscriptionRegistry()
    # A large registry with many users/tabs must not slow a single teardown.
    for i in range(5000):
        registry.subscribe(1000 + i, 10_000 + i, f"tab-{i}")
    registry.subscribe(42, 7, "victim-a")
    registry.subscribe(42, 7, "victim-b")

    class CountingDict(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.items_calls = 0

        def items(self):
            self.items_calls += 1
            return super().items()

    registry._connection_subs = CountingDict(registry._connection_subs)
    registry.unsubscribe(42, 7, "victim-a")
    assert registry.get_subscribers(7) == {42}
    registry.unsubscribe_connection("victim-b", 42)
    assert registry.get_subscribers(7) == set()
    assert registry._connection_subs.items_calls == 0


@pytest.mark.asyncio
async def test_flush_task_wakes_early_on_burst():
    """The interval bounds latency but a burst should not wait for the timer."""
    conn = CountingConnection(
        websocket=AsyncMock(), connection_id="wake", intents=ALL_INTENTS
    )
    conn.state = ConnectionState.READY
    conn.is_selftest = True
    # A long interval: early wakeup is the only way these frames get flushed.
    conn.enable_batching(queue_size=64, max_batch=64, flush_interval=5.0)
    try:
        for i in range(10):
            await conn.send_json({"op": 1, "s": i})
        # Short wait only: the timer is 5 seconds.
        for _ in range(50):
            if conn.write_calls:
                break
            await asyncio.sleep(0.002)
    finally:
        await conn.stop_batching()

    assert conn.write_calls >= 1
    assert len(cast(AsyncMock, conn.websocket.send_json).await_args_list) == 10
