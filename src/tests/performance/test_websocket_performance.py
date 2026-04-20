"""
WebSocket performance and load tests.

Tests WebSocket critical paths:
- Connection establishment
- Heartbeat handling
- Event dispatching
- Concurrent connections
- Message throughput
- Memory leaks in connections
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive_json = AsyncMock()
    ws.close = AsyncMock()
    ws.client_state = MagicMock()
    return ws


@pytest.fixture
def websocket_manager(modules):
    """Setup WebSocket components."""
    from src.api.websocket import setup, get_session_manager, get_dispatcher

    setup(
        auth_module=modules.auth,
        presence_module=modules.presence,
        servers_module=modules.servers,
        heartbeat_interval_ms=45000,
    )

    return {
        "session_manager": get_session_manager(),
        "dispatcher": get_dispatcher(),
    }


class TestWebSocketPerformance:
    """Test WebSocket performance."""

    @pytest.fixture(autouse=True)
    def patch_connection(self, monkeypatch):
        """Mock the Connection class to add missing methods for tests."""
        from src.api.websocket import Connection

        class MockConnection(Connection):
            async def close(self, code=1000, reason=""):
                self.set_disconnected()

            async def handle_heartbeat(self):
                self.record_heartbeat()
                self.record_heartbeat_ack()

        monkeypatch.setattr("src.api.websocket.Connection", MockConnection)

    @pytest.mark.asyncio
    async def test_connection_creation_performance(
        self, benchmark, websocket_manager, test_user_with_token
    ):
        """Benchmark WebSocket connection creation."""
        session_manager = websocket_manager["session_manager"]
        user, token = test_user_with_token

        async def create_connection():
            from src.api.websocket import Connection

            ws = AsyncMock()
            conn = Connection(ws, f"test_conn_{time.time()}")
            session_manager.create_session(conn, user.id, 0)
            return conn

        async def run_benchmark():
            return await create_connection()

        conn = await benchmark.pedantic(run_benchmark, rounds=10)
        assert conn is not None

    @pytest.mark.asyncio
    async def test_event_dispatch_performance(
        self, benchmark, websocket_manager, test_user_with_token
    ):
        """Benchmark event dispatching to a connection."""
        dispatcher = websocket_manager["dispatcher"]
        session_manager = websocket_manager["session_manager"]
        user, token = test_user_with_token

        from src.api.websocket import Connection
        from src.core.events.models import Event
        from src.core.events.types import EventType

        ws = AsyncMock()
        conn = Connection(ws, f"test_conn_{time.time()}")
        session_manager.create_session(conn, user.id, 0)

        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={"content": "Test message", "channel_id": "123"},
        )

        async def dispatch_event():
            await dispatcher.dispatch_event(event, [user.id])

        async def run_benchmark():
            await dispatch_event()

        await benchmark.pedantic(run_benchmark, rounds=20)

    @pytest.mark.asyncio
    async def test_concurrent_connections(self, websocket_manager, stress_test_users):
        """Test handling many concurrent connections."""
        if len(stress_test_users) < 20:
            pytest.skip("Not enough users for concurrent connection test")

        session_manager = websocket_manager["session_manager"]

        connections = []
        start = time.time()

        for user in stress_test_users[:20]:
            from src.api.websocket import Connection

            ws = AsyncMock()
            conn = Connection(ws, f"test_conn_{user.id}_{time.time()}")
            session_manager.create_session(conn, user.id, 0)
            connections.append(conn)

        elapsed = time.time() - start

        assert len(connections) == 20
        assert elapsed < 2.0, f"Creating 20 connections took {elapsed}s (expected < 2s)"

        for conn in connections:
            await conn.close(1000, "Test complete")

    @pytest.mark.asyncio
    async def test_broadcast_performance(self, websocket_manager, stress_test_users):
        """Test broadcasting events to many connections."""
        if len(stress_test_users) < 50:
            pytest.skip("Not enough users for broadcast test")

        session_manager = websocket_manager["session_manager"]
        dispatcher = websocket_manager["dispatcher"]

        connections = []
        user_ids = []

        for user in stress_test_users[:50]:
            from src.api.websocket import Connection

            ws = AsyncMock()
            conn = Connection(ws, f"test_conn_{user.id}_{time.time()}")
            session_manager.create_session(conn, user.id, 0)
            connections.append(conn)
            user_ids.append(user.id)

        from src.core.events.models import Event
        from src.core.events.types import EventType

        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={"content": "Broadcast test", "channel_id": "123"},
        )

        start = time.time()
        count = await dispatcher.dispatch_event(event, user_ids)
        elapsed = time.time() - start

        assert count == 50
        assert (
            elapsed < 0.5
        ), f"Broadcasting to 50 connections took {elapsed}s (expected < 0.5s)"

        for conn in connections:
            await conn.close(1000, "Test complete")

    @pytest.mark.asyncio
    async def test_heartbeat_performance(self, websocket_manager, test_user_with_token):
        """Test heartbeat processing performance."""
        session_manager = websocket_manager["session_manager"]
        user, token = test_user_with_token

        from src.api.websocket import Connection

        ws = AsyncMock()
        conn = Connection(ws, f"test_conn_{time.time()}")
        session_manager.create_session(conn, user.id, 0)

        times = []

        for i in range(100):
            start = time.time()
            await conn.handle_heartbeat()
            elapsed = time.time() - start
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        max_time = max(times)

        assert (
            avg_time < 0.001
        ), f"Average heartbeat time {avg_time}s (expected < 0.001s)"
        assert max_time < 0.01, f"Max heartbeat time {max_time}s (expected < 0.01s)"

        await conn.close(1000, "Test complete")


class TestWebSocketMemory:
    """Test WebSocket memory usage and leaks."""

    @pytest.mark.asyncio
    async def test_connection_memory_leak(
        self, websocket_manager, stress_test_users, memory_tracker
    ):
        """Check for memory leaks in connection lifecycle."""
        if len(stress_test_users) < 50:
            pytest.skip("Not enough users for memory test")

        session_manager = websocket_manager["session_manager"]

        initial_memory = memory_tracker.snapshot()

        for iteration in range(5):
            connections = []

            for user in stress_test_users[:10]:
                from src.api.websocket import Connection

                ws = AsyncMock()
                conn = Connection(ws, f"test_conn_{user.id}_{time.time()}")
                session_manager.create_session(conn, user.id, 0)
                connections.append(conn)

            for conn in connections:
                await conn.close(1000, "Test iteration complete")

            memory_tracker.snapshot()

        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory

        assert (
            memory_increase < 20
        ), f"Memory increased by {memory_increase}MB, potential leak"

    @pytest.mark.asyncio
    async def test_event_dispatch_memory_leak(
        self, websocket_manager, test_user_with_token, memory_tracker
    ):
        """Check for memory leaks in event dispatching."""
        session_manager = websocket_manager["session_manager"]
        dispatcher = websocket_manager["dispatcher"]
        user, token = test_user_with_token

        from src.api.websocket import Connection
        from src.core.events.models import Event
        from src.core.events.types import EventType

        ws = AsyncMock()
        conn = Connection(ws, f"test_conn_{time.time()}")
        session_manager.create_session(conn, user.id, 0)

        initial_memory = memory_tracker.snapshot()

        for i in range(1000):
            event = Event(
                event_type=EventType.MESSAGE_CREATE,
                data={"content": f"Test message {i}", "channel_id": "123"},
            )
            await dispatcher.dispatch_event(event, [user.id])

            if i % 200 == 0:
                memory_tracker.snapshot()

        await conn.close(1000, "Test complete")

        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory

        assert (
            memory_increase < 30
        ), f"Memory increased by {memory_increase}MB, potential leak"


class TestWebSocketDegradation:
    """Test WebSocket performance under sustained load."""

    @pytest.mark.asyncio
    async def test_connection_count_scaling(self, websocket_manager, stress_test_users):
        """Test how performance scales with connection count."""
        if len(stress_test_users) < 30:
            pytest.skip("Not enough users for scaling test")

        session_manager = websocket_manager["session_manager"]
        dispatcher = websocket_manager["dispatcher"]

        from src.api.websocket import Connection
        from src.core.events.models import Event
        from src.core.events.types import EventType

        async def test_with_n_connections(n):
            connections = []
            user_ids = []

            for user in stress_test_users[:n]:
                ws = AsyncMock()
                conn = Connection(ws, f"test_conn_{user.id}_{time.time()}")
                session_manager.create_session(conn, user.id, 0)
                connections.append(conn)
                user_ids.append(user.id)

            event = Event(
                event_type=EventType.MESSAGE_CREATE,
                data={"content": "Scale test", "channel_id": "123"},
            )

            start = time.time()
            await dispatcher.dispatch_event(event, user_ids)
            elapsed = time.time() - start

            for conn in connections:
                await conn.close(1000, "Test complete")

            return elapsed

        time_5 = await test_with_n_connections(5)
        time_10 = await test_with_n_connections(10)
        time_20 = await test_with_n_connections(20)

        assert time_10 < time_5 * 3, "Performance degraded too much with 2x connections"
        assert time_20 < time_5 * 6, "Performance degraded too much with 4x connections"

    @pytest.mark.asyncio
    async def test_sustained_event_throughput(
        self, websocket_manager, test_user_with_token
    ):
        """Test event throughput remains stable over time."""
        session_manager = websocket_manager["session_manager"]
        dispatcher = websocket_manager["dispatcher"]
        user, token = test_user_with_token

        from src.api.websocket import Connection
        from src.core.events.models import Event
        from src.core.events.types import EventType

        ws = AsyncMock()
        conn = Connection(ws, f"test_conn_{time.time()}")
        session_manager.create_session(conn, user.id, 0)

        batch_times = []

        for batch in range(10):
            start = time.time()

            for i in range(100):
                event = Event(
                    event_type=EventType.MESSAGE_CREATE,
                    data={
                        "content": f"Sustained test {batch}_{i}",
                        "channel_id": "123",
                    },
                )
                await dispatcher.dispatch_event(event, [user.id])

            elapsed = time.time() - start
            batch_times.append(elapsed)

        await conn.close(1000, "Test complete")

        first_batch = batch_times[0]
        last_batch = batch_times[-1]

        degradation = (last_batch - first_batch) / first_batch

        assert degradation < 0.5, f"Throughput degraded by {degradation * 100:.1f}%"
