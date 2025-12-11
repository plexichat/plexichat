"""Tests for gateway event dispatching."""

import pytest
from unittest.mock import AsyncMock

from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.connection import Connection, ConnectionState
from src.core import events
from src.core.events.types import GatewayIntent


class TestGatewayDispatcher:
    """Tests for GatewayDispatcher class."""

    def test_dispatcher_initialization(self, dispatcher):
        """Test dispatcher initializes correctly."""
        assert dispatcher is not None

    @pytest.mark.asyncio
    async def test_send_hello(self, dispatcher, connection):
        """Test sending HELLO opcode."""
        connection.state = ConnectionState.CONNECTED
        result = await dispatcher.send_hello(connection)

        assert result is True
        connection.websocket.send_json.assert_called()
        call_args = connection.websocket.send_json.call_args[0][0]
        assert call_args["op"] == GatewayOpcode.HELLO
        assert "heartbeat_interval" in call_args["d"]

    @pytest.mark.asyncio
    async def test_send_heartbeat_ack(self, dispatcher, connection):
        """Test sending HEARTBEAT_ACK opcode."""
        connection.state = ConnectionState.READY
        result = await dispatcher.send_heartbeat_ack(connection)

        assert result is True
        connection.websocket.send_json.assert_called()
        call_args = connection.websocket.send_json.call_args[0][0]
        assert call_args["op"] == GatewayOpcode.HEARTBEAT_ACK

    @pytest.mark.asyncio
    async def test_send_invalid_session(self, dispatcher, connection):
        """Test sending INVALID_SESSION opcode."""
        connection.state = ConnectionState.READY
        result = await dispatcher.send_invalid_session(connection, resumable=True)

        assert result is True
        connection.websocket.send_json.assert_called()
        call_args = connection.websocket.send_json.call_args[0][0]
        assert call_args["op"] == GatewayOpcode.INVALID_SESSION

    @pytest.mark.asyncio
    async def test_send_reconnect(self, dispatcher, connection):
        """Test sending RECONNECT opcode."""
        connection.state = ConnectionState.READY
        result = await dispatcher.send_reconnect(connection)

        assert result is True
        connection.websocket.send_json.assert_called()
        call_args = connection.websocket.send_json.call_args[0][0]
        assert call_args["op"] == GatewayOpcode.RECONNECT


class TestEventDispatch:
    """Tests for event dispatch to connections."""

    @pytest.mark.asyncio
    async def test_dispatch_event_to_users(self, dispatcher, session_manager, mock_websocket):
        """Test dispatching event to users."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(conn, user_id=12345, intents=GatewayIntent.all_intents())

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        count = await dispatcher.dispatch_event(event, [12345])
        assert count == 1

    @pytest.mark.asyncio
    async def test_dispatch_event_filters_by_intents(self, dispatcher, session_manager, mock_websocket):
        """Test event dispatch filters by intents."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(conn, user_id=12345, intents=GatewayIntent.GUILDS)

        event = events.create_guild_member_add(server_id=1, user_id=2)

        count = await dispatcher.dispatch_event(event, [12345])
        assert count == 0

    @pytest.mark.asyncio
    async def test_dispatch_event_to_multiple_users(self, dispatcher, session_manager, mock_websocket):
        """Test dispatching event to multiple users."""
        for user_id in [111, 222, 333]:
            conn = Connection(
                websocket=AsyncMock(),
                connection_id=session_manager.generate_connection_id(),
                heartbeat_interval_ms=45000,
            )
            session_manager.add_connection(conn)
            session_manager.create_session(conn, user_id=user_id, intents=GatewayIntent.all_intents())

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        count = await dispatcher.dispatch_event(event, [111, 222, 333])
        assert count == 3

    @pytest.mark.asyncio
    async def test_dispatch_event_no_matching_users(self, dispatcher, session_manager):
        """Test dispatching event with no matching users."""
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )

        count = await dispatcher.dispatch_event(event, [99999])
        assert count == 0

    @pytest.mark.asyncio
    async def test_dispatch_to_connection(self, dispatcher, authenticated_connection):
        """Test dispatching event to specific connection."""
        authenticated_connection.intents = GatewayIntent.all_intents()
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        result = await dispatcher.dispatch_to_connection(authenticated_connection, event)
        assert result is True

    @pytest.mark.asyncio
    async def test_dispatch_to_connection_filtered(self, dispatcher, authenticated_connection):
        """Test dispatch to connection filtered by intents."""
        authenticated_connection.intents = GatewayIntent.GUILDS
        event = events.create_guild_member_add(server_id=1, user_id=2)

        result = await dispatcher.dispatch_to_connection(authenticated_connection, event)
        assert result is False


class TestDispatchRaw:
    """Tests for raw gateway message dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_raw_dispatch_opcode(self, dispatcher, authenticated_connection):
        """Test dispatching raw DISPATCH opcode."""
        result = await dispatcher.dispatch_raw(
            authenticated_connection,
            GatewayOpcode.DISPATCH,
            {"content": "test"},
            event_type="MESSAGE_CREATE",
        )

        assert result is True
        call_args = authenticated_connection.websocket.send_json.call_args[0][0]
        assert call_args["op"] == GatewayOpcode.DISPATCH
        assert call_args["t"] == "MESSAGE_CREATE"
        assert "s" in call_args

    @pytest.mark.asyncio
    async def test_dispatch_raw_increments_sequence(self, dispatcher, authenticated_connection):
        """Test dispatch raw increments sequence."""
        initial_seq = authenticated_connection.sequence

        await dispatcher.dispatch_raw(
            authenticated_connection,
            GatewayOpcode.DISPATCH,
            {},
            event_type="TEST",
        )

        assert authenticated_connection.sequence == initial_seq + 1

    @pytest.mark.asyncio
    async def test_dispatch_raw_records_event(self, dispatcher, session_manager, authenticated_connection):
        """Test dispatch raw records event for replay."""
        await dispatcher.dispatch_raw(
            authenticated_connection,
            GatewayOpcode.DISPATCH,
            {"test": "data"},
            event_type="TEST_EVENT",
        )

        events_list = session_manager.get_replay_events(
            authenticated_connection.session_id, 0
        )
        assert len(events_list) >= 1


class TestEventCallback:
    """Tests for event module callback."""

    def test_on_event_callback(self, dispatcher, session_manager, mock_websocket):
        """Test on_event callback from events module."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(conn, user_id=12345, intents=GatewayIntent.all_intents())

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        dispatcher.on_event(event, [12345])


class TestReplayEvents:
    """Tests for event replay."""

    @pytest.mark.asyncio
    async def test_replay_events(self, dispatcher, session_manager, authenticated_connection):
        """Test replaying events after resume."""
        for i in range(5):
            await dispatcher.dispatch_raw(
                authenticated_connection,
                GatewayOpcode.DISPATCH,
                {"index": i},
                event_type="TEST",
            )

        authenticated_connection.websocket.send_json.reset_mock()

        count = await dispatcher.replay_events(authenticated_connection, 2)
        assert count == 3

    @pytest.mark.asyncio
    async def test_replay_events_no_session(self, dispatcher, connection):
        """Test replay events with no session."""
        count = await dispatcher.replay_events(connection, 0)
        assert count == 0
