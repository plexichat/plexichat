"""Tests for WebSocket connection management."""

import pytest
import time
from src.api.websocket.connection import ConnectionState


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_connection_states_exist(self):
        """Test all connection states exist."""
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.IDENTIFYING.value == "identifying"
        assert ConnectionState.READY.value == "ready"
        assert ConnectionState.RESUMING.value == "resuming"
        assert ConnectionState.DISCONNECTING.value == "disconnecting"
        assert ConnectionState.DISCONNECTED.value == "disconnected"


class TestConnection:
    """Tests for Connection class."""

    def test_connection_initialization(self, connection):
        """Test connection initializes correctly."""
        assert connection.connection_id is not None
        assert connection.state == ConnectionState.CONNECTING
        assert connection.user_id is None
        assert connection.session_id is None
        assert connection.sequence == 0
        assert connection.intents == 0
        assert connection.compress is False

    def test_connection_not_authenticated_initially(self, connection):
        """Test connection is not authenticated initially."""
        assert connection.is_authenticated is False

    def test_connection_is_alive_initially(self, connection):
        """Test connection is alive initially."""
        assert connection.is_alive is True

    def test_connection_is_not_alive_when_disconnected(self, connection):
        """Test connection is not alive when disconnected."""
        connection.set_disconnected()
        assert connection.is_alive is False

    def test_record_heartbeat(self, connection):
        """Test recording heartbeat updates timestamp."""
        old_heartbeat = connection.last_heartbeat
        time.sleep(0.01)
        connection.record_heartbeat()
        assert connection.last_heartbeat > old_heartbeat
        assert connection.missed_heartbeats == 0

    def test_record_heartbeat_ack(self, connection):
        """Test recording heartbeat ACK updates timestamp."""
        old_ack = connection.last_heartbeat_ack
        time.sleep(0.01)
        connection.record_heartbeat_ack()
        assert connection.last_heartbeat_ack > old_ack

    def test_increment_sequence(self, connection):
        """Test incrementing sequence number."""
        assert connection.sequence == 0
        seq1 = connection.increment_sequence()
        assert seq1 == 1
        assert connection.sequence == 1
        seq2 = connection.increment_sequence()
        assert seq2 == 2
        assert connection.sequence == 2

    def test_set_identified(self, connection):
        """Test setting connection as identified."""
        connection.set_identified(
            user_id=12345,
            session_id="test_session",
            intents=513,
        )
        assert connection.user_id == 12345
        assert connection.session_id == "test_session"
        assert connection.intents == 513
        assert connection.state == ConnectionState.READY
        assert connection.identified_at is not None

    def test_is_authenticated_after_identify(self, connection):
        """Test connection is authenticated after identify."""
        connection.set_identified(
            user_id=12345,
            session_id="test_session",
            intents=513,
        )
        assert connection.is_authenticated is True

    def test_set_resuming(self, connection):
        """Test setting connection as resuming."""
        connection.set_resuming()
        assert connection.state == ConnectionState.RESUMING

    def test_set_disconnecting(self, connection):
        """Test setting connection as disconnecting."""
        connection.set_disconnecting()
        assert connection.state == ConnectionState.DISCONNECTING

    def test_set_disconnected(self, connection):
        """Test setting connection as disconnected."""
        connection.set_disconnected()
        assert connection.state == ConnectionState.DISCONNECTED

    def test_enable_compression(self, connection):
        """Test enabling compression."""
        assert connection.compress is False
        connection.enable_compression()
        assert connection.compress is True

    def test_to_dict(self, connection):
        """Test converting connection to dictionary."""
        result = connection.to_dict()
        assert "connection_id" in result
        assert "state" in result
        assert "user_id" in result
        assert "session_id" in result
        assert "sequence" in result
        assert "intents" in result
        assert "compress" in result
        assert "connected_at" in result

    def test_to_dict_authenticated(self, authenticated_connection):
        """Test to_dict for authenticated connection."""
        result = authenticated_connection.to_dict()
        assert result["user_id"] == 12345
        assert result["session_id"] is not None
        assert result["state"] == "ready"


class TestConnectionRateLimit:
    """Tests for connection rate limiting."""

    def test_check_rate_limit_allows_initial_requests(self, connection):
        """Test rate limit allows initial requests."""
        for _ in range(10):
            assert connection.check_rate_limit(120) is True

    def test_check_rate_limit_blocks_after_limit(self, connection):
        """Test rate limit blocks after limit exceeded."""
        for _ in range(120):
            connection.check_rate_limit(120)
        assert connection.check_rate_limit(120) is False

    def test_check_rate_limit_resets_after_window(self, connection):
        """Test rate limit resets after time window."""
        for _ in range(120):
            connection.check_rate_limit(120)
        assert connection.check_rate_limit(120) is False

        connection.event_window_start = time.monotonic() - 61
        assert connection.check_rate_limit(120) is True


class TestConnectionSendJson:
    """Tests for connection send_json method."""

    @pytest.mark.asyncio
    async def test_send_json_success(self, connection, mock_websocket):
        """Test send_json succeeds."""
        connection.state = ConnectionState.READY
        result = await connection.send_json({"op": 11})
        assert result is True
        mock_websocket.send_json.assert_called_once_with({"op": 11})

    @pytest.mark.asyncio
    async def test_send_json_fails_when_disconnected(self, connection):
        """Test send_json fails when disconnected."""
        connection.set_disconnected()
        result = await connection.send_json({"op": 11})
        assert result is False

    @pytest.mark.asyncio
    async def test_send_json_fails_when_disconnecting(self, connection):
        """Test send_json fails when disconnecting."""
        connection.set_disconnecting()
        result = await connection.send_json({"op": 11})
        assert result is False

    @pytest.mark.asyncio
    async def test_send_json_handles_exception(self, connection, mock_websocket):
        """Test send_json handles exceptions."""
        connection.state = ConnectionState.READY
        mock_websocket.send_json.side_effect = Exception("Send failed")
        result = await connection.send_json({"op": 11})
        assert result is False


class TestConnectionLatency:
    """Tests for connection latency calculation."""

    def test_latency_calculation(self, connection):
        """Test latency is calculated correctly."""
        connection.last_heartbeat = time.monotonic()
        time.sleep(0.01)
        connection.record_heartbeat_ack()
        latency = connection.latency_ms
        assert latency >= 10
        assert latency < 100


class TestConnectionAlive:
    """Tests for connection alive check."""

    def test_is_alive_true_within_timeout(self, connection):
        """Test is_alive is True within timeout."""
        connection.state = ConnectionState.READY
        assert connection.is_alive is True

    def test_is_alive_false_after_timeout(self, connection):
        """Test is_alive is False after timeout."""
        connection.state = ConnectionState.READY
        connection.last_heartbeat = time.monotonic() - 100
        assert connection.is_alive is False

    def test_is_alive_false_when_disconnecting(self, connection):
        """Test is_alive is False when disconnecting."""
        connection.set_disconnecting()
        assert connection.is_alive is False
