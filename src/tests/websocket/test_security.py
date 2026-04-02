"""Comprehensive WebSocket security tests."""

import pytest
import time

from src.api.websocket.opcodes import GatewayOpcode, GatewayCloseCode
from src.api.websocket.connection import Connection, ConnectionState
from src.core.events.types import GatewayIntent


class TestAuthenticationBypass:
    """Tests for authentication bypass vulnerabilities."""

    @pytest.mark.asyncio
    async def test_opcode_without_authentication(self, opcode_handler, connection):
        """Test that opcodes requiring auth fail without authentication."""
        opcodes_requiring_auth = [
            GatewayOpcode.PRESENCE_UPDATE,
            GatewayOpcode.VOICE_STATE_UPDATE,
            GatewayOpcode.REQUEST_GUILD_MEMBERS,
            GatewayOpcode.VOICE_CONNECT,
            GatewayOpcode.VOICE_DISCONNECT,
            GatewayOpcode.VOICE_SDP_OFFER,
            GatewayOpcode.VOICE_ICE_CANDIDATE,
            GatewayOpcode.VOICE_SPEAKING,
            GatewayOpcode.VOICE_QUALITY,
        ]

        for opcode in opcodes_requiring_auth:
            response_op, response_data, close_code = await opcode_handler.handle(
                connection,
                opcode,
                {"test": "data"},
            )
            assert close_code == GatewayCloseCode.NOT_AUTHENTICATED, (
                f"Opcode {opcode} should require auth"
            )

    @pytest.mark.asyncio
    async def test_cannot_identify_twice(
        self, opcode_handler, authenticated_connection, sample_identify_payload
    ):
        """Test that identify cannot be called twice."""
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )
        assert close_code == GatewayCloseCode.ALREADY_AUTHENTICATED

    @pytest.mark.asyncio
    async def test_empty_token_rejected(self, opcode_handler, connection):
        """Test that empty token is rejected."""
        payload = {"token": "", "intents": 513}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )
        assert close_code == GatewayCloseCode.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_missing_token_rejected(self, opcode_handler, connection):
        """Test that missing token is rejected."""
        payload = {"intents": 513}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )
        assert close_code == GatewayCloseCode.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_malformed_token_rejected(
        self, opcode_handler, connection, mock_auth_module
    ):
        """Test that malformed token is rejected."""
        mock_auth_module.verify_token.side_effect = Exception("Invalid token format")
        payload = {"token": "malformed:::token", "intents": 513}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )
        assert close_code == GatewayCloseCode.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_expired_token_rejected(
        self, opcode_handler, connection, mock_auth_module
    ):
        """Test that expired token is rejected."""
        mock_auth_module.verify_token.side_effect = Exception("Token expired")
        payload = {"token": "expired_token", "intents": 513}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )
        assert close_code == GatewayCloseCode.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_resume_with_mismatched_user(
        self, opcode_handler, connection, session_manager, mock_auth_module
    ):
        """Test resume fails when user ID doesn't match session."""
        from dataclasses import dataclass

        @dataclass
        class TokenInfo:
            user_id: int
            permissions: dict

        session = session_manager.create_session(connection, user_id=999, intents=513)
        mock_auth_module.verify_token.return_value = TokenInfo(
            user_id=12345, permissions={}
        )

        resume_payload = {
            "token": "test_token",
            "session_id": session.session_id,
            "seq": 0,
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            resume_payload,
        )
        assert response_op == GatewayOpcode.INVALID_SESSION


class TestOpcodeManipulation:
    """Tests for opcode manipulation attacks."""

    @pytest.mark.asyncio
    async def test_invalid_opcode_rejected(self, opcode_handler, connection):
        """Test that invalid opcode is rejected."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            999,
            {},
        )
        assert close_code == GatewayCloseCode.UNKNOWN_OPCODE

    @pytest.mark.asyncio
    async def test_negative_opcode_rejected(self, opcode_handler, connection):
        """Test that negative opcode is rejected."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            -1,
            {},
        )
        assert close_code == GatewayCloseCode.UNKNOWN_OPCODE

    @pytest.mark.asyncio
    async def test_opcode_with_wrong_payload_type(self, opcode_handler, connection):
        """Test opcodes reject wrong payload types."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            None,
        )
        assert close_code == GatewayCloseCode.DECODE_ERROR

    @pytest.mark.asyncio
    async def test_resume_with_missing_fields(self, opcode_handler, connection):
        """Test resume rejects missing required fields."""
        payloads = [
            {},
            {"token": "test"},
            {"session_id": "test"},
            {"seq": 0},
            {"token": "test", "seq": 0},
            {"session_id": "test", "seq": 0},
        ]
        for payload in payloads:
            response_op, response_data, close_code = await opcode_handler.handle(
                connection,
                GatewayOpcode.RESUME,
                payload,
            )
            assert response_op == GatewayOpcode.INVALID_SESSION

    @pytest.mark.asyncio
    async def test_voice_connect_with_invalid_channel_id(
        self, opcode_handler, authenticated_connection
    ):
        """Test voice connect rejects invalid channel ID."""
        payloads = [
            {"channel_id": "not_a_number"},
            {"channel_id": None},
            {"channel_id": []},
            {"channel_id": {}},
        ]
        for payload in payloads:
            response_op, response_data, close_code = await opcode_handler.handle(
                authenticated_connection,
                GatewayOpcode.VOICE_CONNECT,
                payload,
            )
            assert close_code == GatewayCloseCode.DECODE_ERROR

    @pytest.mark.asyncio
    async def test_voice_sdp_offer_missing_fields(
        self, opcode_handler, authenticated_connection
    ):
        """Test voice SDP offer rejects missing required fields."""
        payloads = [
            {},
            {"channel_id": 123},
            {"sdp": "test_sdp"},
            {"channel_id": "invalid", "sdp": "test"},
        ]
        for payload in payloads:
            response_op, response_data, close_code = await opcode_handler.handle(
                authenticated_connection,
                GatewayOpcode.VOICE_SDP_OFFER,
                payload,
            )
            assert close_code == GatewayCloseCode.DECODE_ERROR


class TestRateLimiting:
    """Tests for rate limiting bypass attempts."""

    @pytest.mark.asyncio
    async def test_connection_rate_limit_enforced(self, connection):
        """Test connection enforces rate limits."""
        for _ in range(120):
            assert connection.check_rate_limit(120) is True
        assert connection.check_rate_limit(120) is False

    @pytest.mark.asyncio
    async def test_rate_limit_per_connection(
        self, dispatcher, session_manager, mock_websocket
    ):
        """Test rate limits are enforced per connection."""
        conn1 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        conn2 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )

        session_manager.add_connection(conn1)
        session_manager.add_connection(conn2)
        session_manager.create_session(
            conn1, user_id=1, intents=GatewayIntent.all_intents()
        )
        session_manager.create_session(
            conn2, user_id=2, intents=GatewayIntent.all_intents()
        )

        for _ in range(120):
            conn1.check_rate_limit(120)
        assert conn1.check_rate_limit(120) is False
        assert conn2.check_rate_limit(120) is True

    @pytest.mark.asyncio
    async def test_max_connections_per_user_enforced(
        self, opcode_handler, session_manager, mock_websocket, sample_identify_payload
    ):
        """Test maximum connections per user is enforced."""
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

        conn_overflow = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn_overflow)
        response_op, response_data, close_code = await opcode_handler.handle(
            conn_overflow,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )
        assert close_code == GatewayCloseCode.RATE_LIMITED

    @pytest.mark.asyncio
    async def test_rate_limit_window_resets(self, connection):
        """Test rate limit window resets after timeout."""
        for _ in range(120):
            connection.check_rate_limit(120)
        assert connection.check_rate_limit(120) is False

        connection.event_window_start = time.monotonic() - 61
        assert connection.check_rate_limit(120) is True

    @pytest.mark.asyncio
    async def test_rapid_identify_attempts_blocked(
        self, opcode_handler, session_manager, mock_websocket, sample_identify_payload
    ):
        """Test rapid identify attempts from same user are blocked."""
        connections = []
        for i in range(6):
            conn = Connection(
                websocket=mock_websocket,
                connection_id=session_manager.generate_connection_id(),
                heartbeat_interval_ms=45000,
            )
            session_manager.add_connection(conn)
            connections.append(conn)

        for i in range(5):
            response_op, response_data, close_code = await opcode_handler.handle(
                connections[i],
                GatewayOpcode.IDENTIFY,
                sample_identify_payload,
            )
            assert close_code is None

        response_op, response_data, close_code = await opcode_handler.handle(
            connections[5],
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )
        assert close_code == GatewayCloseCode.RATE_LIMITED


class TestConnectionFlooding:
    """Tests for connection flooding attacks."""

    def test_connection_tracking(self, session_manager):
        """Test connection tracking prevents resource exhaustion."""
        stats = session_manager.get_stats()
        assert "total_connections" in stats
        assert "active_connections" in stats
        assert "total_sessions" in stats
        assert "unique_users" in stats

    def test_user_connection_limit(self, session_manager):
        """Test user connection limit prevents flooding."""
        assert session_manager.can_user_connect(1) is True

        for i in range(5):
            session_manager._user_connections.setdefault(1, set()).add(f"conn_{i}")

        assert session_manager.can_user_connect(1) is False

    def test_connection_cleanup_on_disconnect(self, session_manager, mock_websocket):
        """Test connections are properly cleaned up."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(conn, user_id=1, intents=513)

        assert session_manager.get_user_connection_count(1) == 1

        session_manager.remove_connection(conn.connection_id)
        assert session_manager.get_user_connection_count(1) == 0

    def test_stale_session_cleanup(self, session_manager, mock_websocket):
        """Test stale sessions are cleaned up."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session = session_manager.create_session(conn, user_id=1, intents=513)
        session_manager.remove_connection(conn.connection_id)

        session.last_activity = time.monotonic() - 70

        cleaned = session_manager.cleanup_stale_sessions()
        assert cleaned == 1
        assert session_manager.get_session(session.session_id) is None


class TestInvalidIntents:
    """Tests for invalid intent handling."""

    @pytest.mark.asyncio
    async def test_negative_intents_rejected(self, opcode_handler, connection):
        """Test negative intents are rejected."""
        payload = {"token": "test_token", "intents": -1}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )
        assert close_code == GatewayCloseCode.INVALID_INTENTS

    @pytest.mark.asyncio
    async def test_too_large_intents_rejected(self, opcode_handler, connection):
        """Test intents larger than all_intents are rejected."""
        from src.api.websocket.intents import ALL_INTENTS

        payload = {"token": "test_token", "intents": ALL_INTENTS + 1}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )
        assert close_code == GatewayCloseCode.INVALID_INTENTS

    @pytest.mark.asyncio
    async def test_non_integer_intents_rejected(self, opcode_handler, connection):
        """Test non-integer intents are rejected."""
        payloads = [
            {"token": "test_token", "intents": "not_an_int"},
            {"token": "test_token", "intents": []},
            {"token": "test_token", "intents": {}},
            {"token": "test_token", "intents": None},
        ]
        for payload in payloads:
            response_op, response_data, close_code = await opcode_handler.handle(
                connection,
                GatewayOpcode.IDENTIFY,
                payload,
            )
            assert close_code == GatewayCloseCode.INVALID_INTENTS

    @pytest.mark.asyncio
    async def test_invalid_intents_bitmask(self, opcode_handler, connection):
        """Test invalid intent bitmask is rejected."""
        payload = {"token": "test_token", "intents": 2**32}
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            payload,
        )
        assert close_code == GatewayCloseCode.INVALID_INTENTS


class TestMaliciousPayloads:
    """Tests for malicious payload handling."""

    @pytest.mark.asyncio
    async def test_extremely_large_sequence_number(
        self, opcode_handler, connection, session_manager
    ):
        """Test extremely large sequence numbers are handled."""
        session = session_manager.create_session(connection, user_id=1, intents=513)
        resume_payload = {
            "token": "test_token",
            "session_id": session.session_id,
            "seq": 2**63 - 1,
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            resume_payload,
        )
        assert response_op in [GatewayOpcode.DISPATCH, GatewayOpcode.INVALID_SESSION]

    @pytest.mark.asyncio
    async def test_negative_sequence_number(
        self, opcode_handler, connection, session_manager
    ):
        """Test negative sequence numbers are handled."""
        session = session_manager.create_session(connection, user_id=1, intents=513)
        resume_payload = {
            "token": "test_token",
            "session_id": session.session_id,
            "seq": -1,
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            resume_payload,
        )
        assert response_op in [GatewayOpcode.DISPATCH, GatewayOpcode.INVALID_SESSION]

    @pytest.mark.asyncio
    async def test_payload_with_recursive_structure(
        self, opcode_handler, authenticated_connection
    ):
        """Test payloads with recursive references don't crash."""
        payload = {
            "status": "online",
            "activities": [{"type": 0, "name": "Test" * 1000}],
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.PRESENCE_UPDATE,
            payload,
        )
        assert close_code is None

    @pytest.mark.asyncio
    async def test_payload_with_special_characters(
        self, opcode_handler, authenticated_connection
    ):
        """Test payloads with special characters are handled."""
        payload = {
            "status": "online",
            "custom_status": "\x00\x01\x02\xff",
            "custom_emoji": "<script>alert('xss')</script>",
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.PRESENCE_UPDATE,
            payload,
        )
        assert close_code is None

    @pytest.mark.asyncio
    async def test_payload_with_unicode_overflow(
        self, opcode_handler, authenticated_connection
    ):
        """Test payloads with unicode overflow attempts."""
        payload = {
            "status": "online",
            "custom_status": "\U0010ffff" * 100,
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.PRESENCE_UPDATE,
            payload,
        )
        assert close_code is None

    @pytest.mark.asyncio
    async def test_empty_payload_handling(
        self, opcode_handler, authenticated_connection
    ):
        """Test empty payloads are handled gracefully."""
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.PRESENCE_UPDATE,
            None,
        )
        assert close_code is None

        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.PRESENCE_UPDATE,
            {},
        )
        assert close_code is None

    @pytest.mark.asyncio
    async def test_payload_with_wrong_types(
        self, opcode_handler, authenticated_connection
    ):
        """Test payloads with wrong types are handled."""
        payload = {
            "status": 12345,
            "activities": "not_a_list",
            "custom_status": [],
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            authenticated_connection,
            GatewayOpcode.PRESENCE_UPDATE,
            payload,
        )
        assert close_code is None


class TestSessionHijacking:
    """Tests for session hijacking prevention."""

    @pytest.mark.asyncio
    async def test_resume_with_wrong_token(
        self, opcode_handler, connection, session_manager, mock_auth_module
    ):
        """Test resume fails with wrong token."""
        from dataclasses import dataclass

        @dataclass
        class TokenInfo:
            user_id: int
            permissions: dict

        session = session_manager.create_session(connection, user_id=12345, intents=513)

        mock_auth_module.verify_token.return_value = TokenInfo(
            user_id=99999, permissions={}
        )

        resume_payload = {
            "token": "different_token",
            "session_id": session.session_id,
            "seq": 0,
        }

        new_conn = Connection(
            websocket=connection.websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(new_conn)

        response_op, response_data, close_code = await opcode_handler.handle(
            new_conn,
            GatewayOpcode.RESUME,
            resume_payload,
        )
        assert response_op == GatewayOpcode.INVALID_SESSION

    @pytest.mark.asyncio
    async def test_resume_with_nonexistent_session(self, opcode_handler, connection):
        """Test resume fails with nonexistent session."""
        resume_payload = {
            "token": "test_token",
            "session_id": "nonexistent_session_id",
            "seq": 0,
        }
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.RESUME,
            resume_payload,
        )
        assert response_op == GatewayOpcode.INVALID_SESSION

    @pytest.mark.asyncio
    async def test_resume_with_expired_session(
        self, opcode_handler, connection, session_manager
    ):
        """Test resume fails with expired session."""
        session = session_manager.create_session(connection, user_id=12345, intents=513)
        session.last_activity = time.monotonic() - 70

        resume_payload = {
            "token": "test_token",
            "session_id": session.session_id,
            "seq": 0,
        }

        new_conn = Connection(
            websocket=connection.websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(new_conn)

        response_op, response_data, close_code = await opcode_handler.handle(
            new_conn,
            GatewayOpcode.RESUME,
            resume_payload,
        )
        assert response_op == GatewayOpcode.INVALID_SESSION

    def test_session_id_uniqueness(self, session_manager):
        """Test session IDs are unique."""
        session_ids = set()
        for _ in range(100):
            session_id = session_manager.generate_session_id()
            assert session_id not in session_ids
            session_ids.add(session_id)

    def test_connection_id_uniqueness(self, session_manager):
        """Test connection IDs are unique."""
        connection_ids = set()
        for _ in range(100):
            connection_id = session_manager.generate_connection_id()
            assert connection_id not in connection_ids
            connection_ids.add(connection_id)

    def test_session_isolation(self, session_manager, mock_websocket):
        """Test sessions are isolated between users."""
        conn1 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        conn2 = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )

        session_manager.add_connection(conn1)
        session_manager.add_connection(conn2)

        session1 = session_manager.create_session(conn1, user_id=1, intents=513)
        session2 = session_manager.create_session(conn2, user_id=2, intents=513)

        assert session1.session_id != session2.session_id
        assert not session_manager.can_resume_session(session1.session_id, 2)
        assert not session_manager.can_resume_session(session2.session_id, 1)

    @pytest.mark.asyncio
    async def test_old_connection_disconnected_on_resume(
        self, session_manager, mock_websocket
    ):
        """Test old connection is disconnected when session is resumed."""
        old_conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(old_conn)
        session = session_manager.create_session(old_conn, user_id=1, intents=513)

        new_conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(new_conn)
        session_manager.resume_session(new_conn, session.session_id, 0)

        assert old_conn.state == ConnectionState.DISCONNECTED
        assert new_conn.state == ConnectionState.READY


class TestHeartbeatSecurity:
    """Tests for heartbeat mechanism security."""

    def test_missed_heartbeat_tracking(self, connection):
        """Test missed heartbeats are tracked."""
        assert connection.missed_heartbeats == 0
        connection.missed_heartbeats += 1
        assert connection.missed_heartbeats == 1
        connection.record_heartbeat()
        assert connection.missed_heartbeats == 0

    def test_connection_timeout_detection(self, connection):
        """Test connection timeout is detected."""
        connection.state = ConnectionState.READY
        connection.last_heartbeat = time.monotonic() - 100
        assert connection.is_alive is False

    def test_connection_alive_within_timeout(self, connection):
        """Test connection is alive within timeout."""
        connection.state = ConnectionState.READY
        connection.record_heartbeat()
        assert connection.is_alive is True

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, opcode_handler, connection):
        """Test heartbeat updates timestamp."""
        old_heartbeat = connection.last_heartbeat
        time.sleep(0.01)
        await opcode_handler.handle(connection, GatewayOpcode.HEARTBEAT, None)
        assert connection.last_heartbeat > old_heartbeat

    @pytest.mark.asyncio
    async def test_heartbeat_ack_sent(self, opcode_handler, connection):
        """Test heartbeat ACK is sent."""
        response_op, response_data, close_code = await opcode_handler.handle(
            connection,
            GatewayOpcode.HEARTBEAT,
            None,
        )
        assert response_op == GatewayOpcode.HEARTBEAT_ACK
        assert close_code is None


class TestCompressionSecurity:
    """Tests for compression-related security."""

    @pytest.mark.asyncio
    async def test_compression_enabled_via_identify(
        self, opcode_handler, connection, sample_identify_payload
    ):
        """Test compression can be enabled during identify."""
        sample_identify_payload["compress"] = True
        await opcode_handler.handle(
            connection,
            GatewayOpcode.IDENTIFY,
            sample_identify_payload,
        )
        assert connection.compress is True

    def test_compression_disabled_by_default(self, connection):
        """Test compression is disabled by default."""
        assert connection.compress is False

    def test_enable_compression(self, connection):
        """Test compression can be enabled."""
        connection.enable_compression()
        assert connection.compress is True
        assert connection._zlib_context is not None


def test_typing_recipients_are_limited_to_channel_viewers(opcode_handler):
    """Typing events should only reach users who can view the channel."""
    opcode_handler._servers.get_member_user_ids.return_value = [2, 3, 4]
    opcode_handler._servers.get_channel.side_effect = (
        lambda channel_id, user_id: object() if user_id in {2, 4} else None
    )

    recipients = opcode_handler._get_typing_recipient_ids(1, 55, 77)

    assert recipients == [2, 4]
    opcode_handler._servers.get_member_user_ids.assert_called_once_with(
        77, exclude_user_id=1
    )
