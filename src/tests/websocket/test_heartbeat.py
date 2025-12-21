"""Tests for gateway heartbeat handling."""

import pytest
import time

from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.connection import ConnectionState


class TestHeartbeatHandler:
    """Tests for HEARTBEAT opcode handling."""

    @pytest.mark.asyncio
    async def test_heartbeat_returns_ack(self, opcode_handler, connection):
        """Test heartbeat returns ACK."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.HEARTBEAT,
            42,
        )

        assert response_op == GatewayOpcode.HEARTBEAT_ACK
        assert close_code is None

    @pytest.mark.asyncio
    async def test_heartbeat_updates_last_heartbeat(self, opcode_handler, connection):
        """Test heartbeat updates last_heartbeat timestamp."""
        old_heartbeat = connection.last_heartbeat
        time.sleep(0.01)

        await opcode_handler.handle(
            connection,
            GatewayOpcode.HEARTBEAT,
            42,
        )

        assert connection.last_heartbeat > old_heartbeat

    @pytest.mark.asyncio
    async def test_heartbeat_resets_missed_count(self, opcode_handler, connection):
        """Test heartbeat resets missed heartbeat count."""
        connection.missed_heartbeats = 3

        await opcode_handler.handle(
            connection,
            GatewayOpcode.HEARTBEAT,
            42,
        )

        assert connection.missed_heartbeats == 0

    @pytest.mark.asyncio
    async def test_heartbeat_with_null_data(self, opcode_handler, connection):
        """Test heartbeat with null data still works."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.HEARTBEAT,
            None,
        )

        assert response_op == GatewayOpcode.HEARTBEAT_ACK
        assert close_code is None

    @pytest.mark.asyncio
    async def test_heartbeat_with_sequence_data(self, opcode_handler, connection):
        """Test heartbeat with sequence number data."""
        connection.sequence = 100

        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.HEARTBEAT,
            100,
        )

        assert response_op == GatewayOpcode.HEARTBEAT_ACK

    @pytest.mark.asyncio
    async def test_heartbeat_updates_ack_timestamp(self, opcode_handler, connection):
        """Test heartbeat updates ACK timestamp."""
        old_ack = connection.last_heartbeat_ack
        time.sleep(0.01)

        await opcode_handler.handle(
            connection,
            GatewayOpcode.HEARTBEAT,
            42,
        )

        assert connection.last_heartbeat_ack > old_ack


class TestHeartbeatInterval:
    """Tests for heartbeat interval configuration."""

    def test_session_manager_heartbeat_interval(self, session_manager):
        """Test session manager has heartbeat interval."""
        assert session_manager.heartbeat_interval_ms == 45000

    def test_connection_heartbeat_interval(self, connection):
        """Test connection has heartbeat interval."""
        assert connection.heartbeat_interval_ms == 45000


class TestHeartbeatAlive:
    """Tests for heartbeat-based alive detection."""

    def test_connection_alive_within_interval(self, connection):
        """Test connection is alive within heartbeat interval."""
        connection.state = ConnectionState.READY
        connection.last_heartbeat = time.monotonic()
        assert connection.is_alive is True

    def test_connection_not_alive_after_timeout(self, connection):
        """Test connection is not alive after timeout."""
        connection.state = ConnectionState.READY
        timeout = (connection.heartbeat_interval_ms / 1000) * 2 + 1
        connection.last_heartbeat = time.monotonic() - timeout
        assert connection.is_alive is False

    def test_connection_alive_at_boundary(self, connection):
        """Test connection alive at timeout boundary."""
        connection.state = ConnectionState.READY
        timeout = (connection.heartbeat_interval_ms / 1000) * 2 - 1
        connection.last_heartbeat = time.monotonic() - timeout
        assert connection.is_alive is True


class TestHeartbeatLatency:
    """Tests for heartbeat latency calculation."""

    def test_latency_positive(self, connection):
        """Test latency is positive."""
        connection.last_heartbeat = time.monotonic()
        time.sleep(0.01)
        connection.record_heartbeat_ack()
        assert connection.latency_ms > 0

    def test_latency_reasonable(self, connection):
        """Test latency is reasonable."""
        connection.last_heartbeat = time.monotonic()
        time.sleep(0.05)
        connection.record_heartbeat_ack()
        latency = connection.latency_ms
        assert 40 < latency < 100


class TestMissedHeartbeats:
    """Tests for missed heartbeat tracking."""

    def test_missed_heartbeats_initial(self, connection):
        """Test missed heartbeats starts at 0."""
        assert connection.missed_heartbeats == 0

    def test_missed_heartbeats_increment(self, connection):
        """Test missed heartbeats can be incremented."""
        connection.missed_heartbeats += 1
        assert connection.missed_heartbeats == 1
        connection.missed_heartbeats += 1
        assert connection.missed_heartbeats == 2

    def test_heartbeat_resets_missed(self, connection):
        """Test heartbeat resets missed count."""
        connection.missed_heartbeats = 5
        connection.record_heartbeat()
        assert connection.missed_heartbeats == 0
