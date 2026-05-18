"""Tests for gateway identify flow."""

import pytest

from src.api.websocket.opcodes import GatewayOpcode, GatewayCloseCode
from src.api.websocket.connection import ConnectionState


class TestIdentifyHandler:
    """Tests for IDENTIFY opcode handling."""

    @pytest.mark.asyncio
    async def test_identify_success(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test successful identify."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert response_op == GatewayOpcode.DISPATCH
        assert response_data is not None
        assert response_data["t"] == "READY"
        assert close_code is None
        assert connection.is_authenticated is True

    @pytest.mark.asyncio
    async def test_identify_sets_user_id(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test identify sets user ID."""
        await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert connection.user_id == 12345

    @pytest.mark.asyncio
    async def test_identify_sets_session_id(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test identify sets session ID."""
        await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert connection.session_id is not None
        assert len(connection.session_id) > 0

    @pytest.mark.asyncio
    async def test_identify_sets_intents(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test identify sets intents."""
        await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert connection.intents == sample_identify_payload["intents"]

    @pytest.mark.asyncio
    async def test_identify_sets_state_to_ready(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test identify sets state to READY."""
        await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert connection.state == ConnectionState.READY

    @pytest.mark.asyncio
    async def test_identify_without_token_fails(self, opcode_handler, connection):
        """Test identify without token fails."""
        payload = {"intents": 513}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )

        assert close_code == GatewayCloseCode.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_identify_with_invalid_token_fails(
        self, opcode_handler, connection, mock_auth_module
    ):
        """Test identify with invalid token fails."""
        mock_auth_module.verify_token.side_effect = Exception("Invalid token")

        payload = {"token": "invalid_token", "intents": 513}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )

        assert close_code == GatewayCloseCode.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_identify_with_invalid_intents_fails(
        self, opcode_handler, connection
    ):
        """Test identify with invalid intents fails."""
        payload = {"token": "test_token", "intents": -1}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )

        assert close_code == GatewayCloseCode.INVALID_INTENTS

    @pytest.mark.asyncio
    async def test_identify_already_authenticated_fails(
        self, opcode_handler, authenticated_connection, sample_identify_payload
    ):
        """Test identify when already authenticated fails."""
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert close_code == GatewayCloseCode.ALREADY_AUTHENTICATED

    @pytest.mark.asyncio
    async def test_identify_with_empty_payload_fails(self, opcode_handler, connection):
        """Test identify with empty payload fails."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            None,
        )

        assert close_code == GatewayCloseCode.DECODE_ERROR

    @pytest.mark.asyncio
    async def test_identify_stores_properties(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test identify stores client properties."""
        await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert connection.properties == sample_identify_payload["properties"]

    @pytest.mark.asyncio
    async def test_identify_with_compression(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test identify with compression enabled."""
        sample_identify_payload["compress"] = True
        await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert connection.compress is True

    @pytest.mark.asyncio
    async def test_identify_ready_payload_structure(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test READY payload has correct structure."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        ready_data = response_data["d"]
        assert "v" in ready_data
        assert "user" in ready_data
        assert "guilds" in ready_data
        assert "session_id" in ready_data
        assert ready_data["v"] == 10

    @pytest.mark.asyncio
    async def test_identify_ready_contains_user_info(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test READY payload contains user info."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        user = response_data["d"]["user"]
        assert "id" in user
        assert "username" in user

    @pytest.mark.asyncio
    async def test_identify_increments_sequence(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test identify increments sequence number."""
        assert connection.sequence == 0
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert response_data["s"] == 1
        assert connection.sequence == 1


class TestIdentifyRateLimit:
    """Tests for identify rate limiting."""

    @pytest.mark.asyncio
    async def test_identify_respects_max_connections(
        self, opcode_handler, session_manager, mock_websocket, sample_identify_payload
    ):
        """Test identify respects max connections per user."""
        from src.api.websocket.connection import Connection

        for i in range(5):
            conn = Connection(
                websocket=mock_websocket,
                connection_id=session_manager.generate_connection_id(),
                heartbeat_interval_ms=45000,
            )
            session_manager.add_connection(conn)
            await opcode_handler.handle(
                conn, GatewayOpcode.IDENTIFY, sample_identify_payload
            )

        new_conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(new_conn)
        response_op, response_data, close_code = await opcode_handler.handle(
            new_conn,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )

        assert close_code == GatewayCloseCode.RATE_LIMITED
