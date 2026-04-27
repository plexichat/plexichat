"""Tests for gateway edge cases and error handling."""

import pytest

from src.api.websocket.opcodes import (
    GatewayOpcode,
    GatewayCloseCode,
    is_resumable,
    get_close_message,
)
from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.compression import (
    compress_payload,
    decompress_payload,
    is_compressed,
    ZlibCompressor,
    ZlibDecompressor,
)


class TestOpcodes:
    """Tests for opcode definitions."""

    def test_all_opcodes_exist(self):
        """Test all opcodes are defined."""
        assert GatewayOpcode.DISPATCH == 0
        assert GatewayOpcode.HEARTBEAT == 1
        assert GatewayOpcode.IDENTIFY == 2
        assert GatewayOpcode.PRESENCE_UPDATE == 3
        assert GatewayOpcode.VOICE_STATE_UPDATE == 4
        assert GatewayOpcode.RESUME == 6
        assert GatewayOpcode.RECONNECT == 7
        assert GatewayOpcode.REQUEST_GUILD_MEMBERS == 8
        assert GatewayOpcode.INVALID_SESSION == 9
        assert GatewayOpcode.HELLO == 10
        assert GatewayOpcode.HEARTBEAT_ACK == 11

    def test_unknown_opcode_handling(self, opcode_handler, connection):
        """Test unknown opcode returns error."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            opcode_handler.handle(connection, 99, None)
        )
        assert result[2] == GatewayCloseCode.UNKNOWN_OPCODE


class TestCloseCodes:
    """Tests for close code definitions."""

    def test_all_close_codes_exist(self):
        """Test all close codes are defined."""
        assert GatewayCloseCode.UNKNOWN_ERROR == 4000
        assert GatewayCloseCode.UNKNOWN_OPCODE == 4001
        assert GatewayCloseCode.DECODE_ERROR == 4002
        assert GatewayCloseCode.NOT_AUTHENTICATED == 4003
        assert GatewayCloseCode.AUTHENTICATION_FAILED == 4004
        assert GatewayCloseCode.ALREADY_AUTHENTICATED == 4005
        assert GatewayCloseCode.INVALID_SEQ == 4007
        assert GatewayCloseCode.RATE_LIMITED == 4008
        assert GatewayCloseCode.SESSION_TIMED_OUT == 4009
        assert GatewayCloseCode.INVALID_INTENTS == 4013
        assert GatewayCloseCode.DISALLOWED_INTENTS == 4014

    def test_is_resumable(self):
        """Test is_resumable function."""
        assert is_resumable(GatewayCloseCode.UNKNOWN_ERROR) is True
        assert is_resumable(GatewayCloseCode.RATE_LIMITED) is True
        assert is_resumable(GatewayCloseCode.SESSION_TIMED_OUT) is True
        assert is_resumable(GatewayCloseCode.AUTHENTICATION_FAILED) is False
        assert is_resumable(GatewayCloseCode.INVALID_INTENTS) is False

    def test_get_close_message(self):
        """Test get_close_message function."""
        assert get_close_message(GatewayCloseCode.UNKNOWN_ERROR) == "Unknown error"
        assert (
            get_close_message(GatewayCloseCode.AUTHENTICATION_FAILED)
            == "Authentication failed"
        )
        assert get_close_message(GatewayCloseCode.RATE_LIMITED) == "Rate limited"
        assert get_close_message(9999) == "Unknown close code"


class TestCompression:
    """Tests for compression utilities."""

    def test_compress_payload(self):
        """Test compressing a payload."""
        data = {"op": 0, "d": {"content": "test"}}
        compressed = compress_payload(data)
        assert isinstance(compressed, bytes)
        assert len(compressed) > 0

    def test_decompress_payload(self):
        """Test decompressing a payload."""
        data = {"op": 0, "d": {"content": "test"}}
        compressed = compress_payload(data)
        decompressed = decompress_payload(compressed)
        assert decompressed == data

    def test_decompress_invalid_data(self):
        """Test decompressing invalid data."""
        result = decompress_payload(b"invalid data")
        assert result is None

    def test_is_compressed_true(self):
        """Test is_compressed returns True for compressed data."""
        data = {"test": "data"}
        compressed = compress_payload(data)
        assert is_compressed(compressed) is True

    def test_is_compressed_false(self):
        """Test is_compressed returns False for uncompressed data."""
        assert is_compressed(b"not compressed") is False
        assert is_compressed(b"") is False
        assert is_compressed(b"x") is False


class TestZlibCompressor:
    """Tests for ZlibCompressor class."""

    def test_compressor_compress(self):
        """Test compressor compress method."""
        compressor = ZlibCompressor()
        data = {"op": 0, "d": {"content": "test"}}
        compressed = compressor.compress(data)
        assert isinstance(compressed, bytes)
        assert len(compressed) > 0

    def test_compressor_reset(self):
        """Test compressor reset method."""
        compressor = ZlibCompressor()
        compressor.compress({"test": "data"})
        compressor.reset()
        compressed = compressor.compress({"test": "data2"})
        assert isinstance(compressed, bytes)


class TestZlibDecompressor:
    """Tests for ZlibDecompressor class."""

    def test_decompressor_decompress(self):
        """Test decompressor decompress method."""
        compressor = ZlibCompressor()
        decompressor = ZlibDecompressor()

        data = {"op": 0, "d": {"content": "test"}}
        compressed = compressor.compress(data)
        decompressed = decompressor.decompress(compressed)
        assert decompressed == data

    def test_decompressor_reset(self):
        """Test decompressor reset method."""
        decompressor = ZlibDecompressor()
        decompressor.reset()


class TestConnectionEdgeCases:
    """Tests for connection edge cases."""

    def test_connection_state_transitions(self, connection):
        """Test connection state transitions."""
        assert connection.state == ConnectionState.CONNECTING

        connection.state = ConnectionState.CONNECTED
        assert connection.state == ConnectionState.CONNECTED

        connection.set_resuming()
        assert connection.state == ConnectionState.RESUMING

        connection.set_disconnecting()
        assert connection.state == ConnectionState.DISCONNECTING

        connection.set_disconnected()
        assert connection.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_send_json_with_compression(self, mock_websocket, session_manager):
        """Test send_json with compression enabled."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        conn.state = ConnectionState.READY
        conn.enable_compression()

        result = await conn.send_json({"op": 11})
        assert result is True
        mock_websocket.send_bytes.assert_called()

    def test_connection_properties(self, connection):
        """Test connection properties storage."""
        connection.properties = {"os": "windows", "browser": "test"}
        assert connection.properties["os"] == "windows"
        assert connection.properties["browser"] == "test"


class TestSessionEdgeCases:
    """Tests for session edge cases."""

    def test_session_replay_buffer_limit(self, session_manager, connection):
        """Test session replay buffer has limit."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        for i in range(200):
            event = {"op": 0, "t": "TEST", "s": i + 1, "d": {}}
            session.add_replay_event(event)

        assert len(session.replay_events) <= 100

    def test_session_update_activity(self, session_manager, connection):
        """Test session activity update."""
        import time

        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        old_activity = session.last_activity
        time.sleep(0.01)
        session.update_activity()

        assert session.last_activity > old_activity

    def test_session_manager_stats(self, session_manager, connection):
        """Test session manager statistics."""
        session_manager.add_connection(connection)
        session_manager.create_session(connection, user_id=12345, intents=513)

        stats = session_manager.get_stats()
        assert "total_connections" in stats
        assert "active_connections" in stats
        assert "total_sessions" in stats
        assert "unique_users" in stats
        assert stats["total_connections"] >= 1
        assert stats["total_sessions"] >= 1


class TestHandlerEdgeCases:
    """Tests for handler edge cases."""

    @pytest.mark.asyncio
    async def test_presence_update_not_authenticated(self, opcode_handler, connection):
        """Test presence update when not authenticated."""
        payload = {"status": "online"}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.PRESENCE_UPDATE,
            payload,
        )

        assert close_code == GatewayCloseCode.NOT_AUTHENTICATED

    @pytest.mark.asyncio
    async def test_voice_state_update_not_authenticated(
        self, opcode_handler, connection
    ):
        """Test voice state update when not authenticated."""
        payload = {"channel_id": "123"}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.VOICE_STATE_UPDATE,
            payload,
        )

        assert close_code == GatewayCloseCode.NOT_AUTHENTICATED

    @pytest.mark.asyncio
    async def test_request_guild_members_not_authenticated(
        self, opcode_handler, connection
    ):
        """Test request guild members when not authenticated."""
        payload = {"guild_id": "123"}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.REQUEST_GUILD_MEMBERS,
            payload,
        )

        assert close_code == GatewayCloseCode.NOT_AUTHENTICATED

    @pytest.mark.asyncio
    async def test_presence_update_authenticated(
        self, opcode_handler, authenticated_connection, sample_presence_update_payload
    ):
        """Test presence update when authenticated."""
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.PRESENCE_UPDATE,
            sample_presence_update_payload,
        )

        assert close_code is None
