"""Tests for gateway session resume."""

import pytest
import time

from src.api.websocket.opcodes import GatewayOpcode, GatewayCloseCode
from src.api.websocket.connection import Connection


class TestResumeHandler:
    """Tests for RESUME opcode handling."""

    @pytest.mark.asyncio
    async def test_resume_success(
        self,
        opcode_handler,
        session_manager,
        mock_websocket,
        mock_auth_module,
        sample_identify_payload,
    ):
        """Test successful resume."""
        conn1 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn1)
        await opcode_handler.handle(
            conn1, GatewayOpcode.IDENTIFY, sample_identify_payload
        )
        session_id = conn1.session_id
        seq = conn1.sequence

        conn2 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn2)

        resume_payload = {
            "token": sample_identify_payload["token"],
            "session_id": session_id,
            "seq": seq,
        }

        response_op, response_data, close_code = await opcode_handler.handle(
            conn2,
            GatewayOpcode.RESUME,
            resume_payload,
        )

        assert response_op == GatewayOpcode.DISPATCH
        assert response_data["t"] == "RESUMED"
        assert close_code is None
        assert conn2.is_authenticated is True

    @pytest.mark.asyncio
    async def test_resume_invalid_session_id(self, opcode_handler, connection):
        """Test resume with invalid session ID."""
        resume_payload = {
            "token": "test_token",
            "session_id": "invalid_session_id",
            "seq": 0,
        }

        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            resume_payload,
        )

        assert response_op == GatewayOpcode.INVALID_SESSION
        assert response_data["d"] is False

    @pytest.mark.asyncio
    async def test_resume_invalid_token(
        self,
        opcode_handler,
        session_manager,
        mock_websocket,
        mock_auth_module,
        sample_identify_payload,
    ):
        """Test resume with invalid token."""
        conn1 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn1)
        await opcode_handler.handle(
            conn1, GatewayOpcode.IDENTIFY, sample_identify_payload
        )
        session_id = conn1.session_id

        mock_auth_module.verify_token.side_effect = Exception("Invalid token")

        conn2 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn2)

        resume_payload = {
            "token": "invalid_token",
            "session_id": session_id,
            "seq": 0,
        }

        response_op, response_data, close_code = await opcode_handler.handle(
            conn2,
            GatewayOpcode.RESUME,
            resume_payload,
        )

        assert response_op == GatewayOpcode.INVALID_SESSION

    @pytest.mark.asyncio
    async def test_resume_without_token(self, opcode_handler, connection):
        """Test resume without token."""
        resume_payload = {
            "session_id": "some_session",
            "seq": 0,
        }

        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            resume_payload,
        )

        assert response_op == GatewayOpcode.INVALID_SESSION

    @pytest.mark.asyncio
    async def test_resume_without_session_id(self, opcode_handler, connection):
        """Test resume without session ID."""
        resume_payload = {
            "token": "test_token",
            "seq": 0,
        }

        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            resume_payload,
        )

        assert response_op == GatewayOpcode.INVALID_SESSION

    @pytest.mark.asyncio
    async def test_resume_empty_payload(self, opcode_handler, connection):
        """Test resume with empty payload."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            None,
        )

        assert close_code == GatewayCloseCode.DECODE_ERROR


class TestSessionManagement:
    """Tests for session management."""

    def test_create_session(self, session_manager, connection):
        """Test creating a session."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        assert session is not None
        assert session.session_id is not None
        assert session.user_id == 12345
        assert session.intents == 513

    def test_get_session(self, session_manager, connection):
        """Test getting a session."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        retrieved = session_manager.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_remove_session(self, session_manager, connection):
        """Test removing a session."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        removed = session_manager.remove_session(session.session_id)
        assert removed is not None

        retrieved = session_manager.get_session(session.session_id)
        assert retrieved is None

    def test_can_resume_session_valid(self, session_manager, connection):
        """Test can_resume_session with valid session."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        can_resume = session_manager.can_resume_session(session.session_id, 12345)
        assert can_resume is True

    def test_can_resume_session_wrong_user(self, session_manager, connection):
        """Test can_resume_session with wrong user."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        can_resume = session_manager.can_resume_session(session.session_id, 99999)
        assert can_resume is False

    def test_can_resume_session_invalid_id(self, session_manager):
        """Test can_resume_session with invalid session ID."""
        can_resume = session_manager.can_resume_session("invalid_id", 12345)
        assert can_resume is False


class TestSessionTimeout:
    """Tests for session timeout."""

    def test_session_timeout_removes_stale(self, session_manager, connection):
        """Test stale sessions are removed."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        session.last_activity = time.monotonic() - 120

        session_manager.remove_connection(connection.connection_id)

        removed = session_manager.cleanup_stale_sessions()
        assert removed >= 1

    def test_session_not_removed_if_active(self, session_manager, connection):
        """Test active sessions are not removed."""
        session_manager.add_connection(connection)
        session_manager.create_session(connection, user_id=12345, intents=513)

        removed = session_manager.cleanup_stale_sessions()
        assert removed == 0


class TestEventReplay:
    """Tests for event replay on resume."""

    def test_record_event(self, session_manager, connection):
        """Test recording events for replay."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        event = {"op": 0, "t": "MESSAGE_CREATE", "s": 1, "d": {}}
        session_manager.record_event(session.session_id, event)

        events = session_manager.get_replay_events(session.session_id, 0)
        assert len(events) == 1
        assert events[0]["t"] == "MESSAGE_CREATE"

    def test_get_replay_events_after_sequence(self, session_manager, connection):
        """Test getting events after a sequence number."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        for i in range(5):
            event = {"op": 0, "t": "MESSAGE_CREATE", "s": i + 1, "d": {}}
            session_manager.record_event(session.session_id, event)

        events = session_manager.get_replay_events(session.session_id, 3)
        assert len(events) == 2
        assert events[0]["s"] == 4
        assert events[1]["s"] == 5

    def test_replay_events_limit(self, session_manager, connection):
        """Test replay events are limited."""
        session_manager.add_connection(connection)
        session = session_manager.create_session(connection, user_id=12345, intents=513)

        for i in range(150):
            event = {"op": 0, "t": "MESSAGE_CREATE", "s": i + 1, "d": {}}
            session_manager.record_event(session.session_id, event)

        events = session_manager.get_replay_events(session.session_id, 0)
        assert len(events) <= 100

    def test_get_replay_events_invalid_session(self, session_manager):
        """Test getting replay events for invalid session."""
        events = session_manager.get_replay_events("invalid_id", 0)
        assert events == []
