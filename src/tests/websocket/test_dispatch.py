"""Tests for gateway event dispatching."""

import json

import pytest
from unittest.mock import AsyncMock

from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.connection import Connection, ConnectionState
from src.core import events
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent


class TestGatewayDispatcher:
    """Tests for GatewayDispatcher class."""

    def test_dispatcher_initialization(self, dispatcher):
        """Test dispatcher initializes correctly."""
        assert dispatcher is not None
        assert hasattr(dispatcher, "on_event")
        assert hasattr(dispatcher, "set_event_loop")

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
    async def test_dispatch_event_to_users(
        self, dispatcher, session_manager, mock_websocket
    ):
        """Test dispatching event to users."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(
            conn, user_id=12345, intents=GatewayIntent.all_intents()
        )

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        count = await dispatcher.dispatch_event(event, [12345])
        assert count == 1

    @pytest.mark.asyncio
    async def test_dispatch_event_filters_by_intents(
        self, dispatcher, session_manager, mock_websocket
    ):
        """Test event dispatch filters by intents."""
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(
            conn, user_id=12345, intents=GatewayIntent.GUILDS
        )

        event = events.create_guild_member_add(server_id=1, user_id=2)

        count = await dispatcher.dispatch_event(event, [12345])
        assert count == 0

    @pytest.mark.asyncio
    async def test_dispatch_event_to_multiple_users(
        self, dispatcher, session_manager, mock_websocket
    ):
        """Test dispatching event to multiple users."""
        for user_id in [111, 222, 333]:
            conn = Connection(
                websocket=AsyncMock(),
                connection_id=session_manager.generate_connection_id(),
                heartbeat_interval_ms=45000,
            )
            session_manager.add_connection(conn)
            session_manager.create_session(
                conn, user_id=user_id, intents=GatewayIntent.all_intents()
            )

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

        result = await dispatcher.dispatch_to_connection(
            authenticated_connection, event
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_dispatch_to_connection_filtered(
        self, dispatcher, authenticated_connection
    ):
        """Test dispatch to connection filtered by intents."""
        authenticated_connection.intents = GatewayIntent.GUILDS
        event = events.create_guild_member_add(server_id=1, user_id=2)

        result = await dispatcher.dispatch_to_connection(
            authenticated_connection, event
        )
        assert result is False


class TestDispatchRaw:
    """Tests for raw gateway message dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_raw_dispatch_opcode(
        self, dispatcher, authenticated_connection
    ):
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
    async def test_dispatch_raw_increments_sequence(
        self, dispatcher, authenticated_connection
    ):
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
    async def test_dispatch_raw_records_event(
        self, dispatcher, session_manager, authenticated_connection
    ):
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
        # Exactly one event was dispatched
        assert len(events_list) == 1
        assert events_list[0]["t"] == "TEST_EVENT"
        assert events_list[0]["d"] == {"test": "data"}


class TestSnowflakeSerialization:
    """Snowflake ids must be strings on the wire even for hand-built events.

    The dispatcher applies the shared serializer at the wire boundary as a
    backstop, so an ``Event`` constructed directly (bypassing the emitters)
    cannot leak a numeric id that a JS client would round.
    """

    def test_build_dispatch_payload_stringifies_ids(
        self, dispatcher, authenticated_connection
    ):
        event = Event(
            event_type=EventType.READY,
            data={
                "id": 1234567890123456789,
                "user_id": 1234567890123456789,
                "channel_id": 2,
                "message_ids": [3, 4],
                "count": 5,
            },
        )

        payload = dispatcher._build_dispatch_payload(authenticated_connection, event)

        # The payload must survive a real JSON round-trip with string ids.
        wire = json.loads(json.dumps(payload))
        data = wire["d"]
        assert data["id"] == "1234567890123456789"
        assert data["user_id"] == "1234567890123456789"
        assert data["channel_id"] == "2"
        assert data["message_ids"] == ["3", "4"]
        # Non-id numbers are untouched.
        assert data["count"] == 5

    def test_dispatch_records_stringified_payload(
        self, dispatcher, session_manager, authenticated_connection
    ):
        """The replay buffer must hold the same string ids the client saw."""
        event = Event(
            event_type=EventType.READY,
            data={"user_id": 1234567890123456789},
        )
        dispatcher._build_dispatch_payload(authenticated_connection, event)

        recorded = session_manager.get_replay_events(
            authenticated_connection.session_id, 0
        )
        assert recorded[-1]["d"]["user_id"] == "1234567890123456789"


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
        session_manager.create_session(
            conn, user_id=12345, intents=GatewayIntent.all_intents()
        )

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )

        dispatcher.on_event(event, [12345])


class TestReplayEvents:
    """Tests for event replay."""

    @pytest.mark.asyncio
    async def test_replay_events(
        self, dispatcher, session_manager, authenticated_connection
    ):
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


class TestBroadcastToServerScoping:
    """`broadcast_to_server` must reach server members only, and fail closed.

    Regression guard: the method used to iterate EVERY connected
    authenticated connection, so a server-scoped event leaked to non-members.
    """

    @staticmethod
    def _connect(session_manager, user_id):
        websocket = AsyncMock()
        conn = Connection(
            websocket=websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        session_manager.create_session(
            conn, user_id=user_id, intents=GatewayIntent.all_intents()
        )
        return conn, websocket

    @pytest.mark.asyncio
    async def test_only_members_receive_the_event(
        self, dispatcher, session_manager, monkeypatch
    ):
        """A non-member connection must not receive a server-scoped event."""
        import src.api.websocket as ws_pkg

        member_conn, member_ws = self._connect(session_manager, 111)
        non_member_conn, non_member_ws = self._connect(session_manager, 222)

        servers = AsyncMock()
        servers.get_member_user_ids = lambda server_id: [111]
        monkeypatch.setattr(ws_pkg, "get_servers_module", lambda: servers)

        event = events.create_guild_member_add(server_id=7, user_id=999)
        count = await dispatcher.broadcast_to_server(7, event)

        assert count == 1
        member_ws.send_json.assert_called()
        non_member_ws.send_json.assert_not_called()
        assert non_member_conn.user_id == 222

    @pytest.mark.asyncio
    async def test_unwired_servers_module_fails_closed(
        self, dispatcher, session_manager, monkeypatch
    ):
        """An unwired servers module must broadcast to nobody, not everybody."""
        import src.api.websocket as ws_pkg

        _, ws = self._connect(session_manager, 111)
        monkeypatch.setattr(ws_pkg, "get_servers_module", lambda: None)

        event = events.create_guild_member_add(server_id=7, user_id=999)
        count = await dispatcher.broadcast_to_server(7, event)

        assert count == 0
        ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_membership_lookup_failure_fails_closed(
        self, dispatcher, session_manager, monkeypatch
    ):
        """A failing membership lookup must not over-broadcast."""
        import src.api.websocket as ws_pkg

        _, ws = self._connect(session_manager, 111)

        def _boom(server_id):
            raise RuntimeError("db down")

        servers = AsyncMock()
        servers.get_member_user_ids = _boom
        monkeypatch.setattr(ws_pkg, "get_servers_module", lambda: servers)

        event = events.create_guild_member_add(server_id=7, user_id=999)
        count = await dispatcher.broadcast_to_server(7, event)

        assert count == 0
        ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_excluded_members_are_skipped(
        self, dispatcher, session_manager, monkeypatch
    ):
        """`exclude_user_ids` still applies within the member set."""
        import src.api.websocket as ws_pkg

        _, ws = self._connect(session_manager, 111)

        servers = AsyncMock()
        servers.get_member_user_ids = lambda server_id: [111, 333]
        monkeypatch.setattr(ws_pkg, "get_servers_module", lambda: servers)

        event = events.create_guild_member_add(server_id=7, user_id=999)
        count = await dispatcher.broadcast_to_server(7, event, exclude_user_ids=[111])

        # 333 is a member but has no live connection; 111 is excluded.
        assert count == 0
        ws.send_json.assert_not_called()
