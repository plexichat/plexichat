"""RESUME replay must re-check channel/conversation access at replay time.

A gateway session outlives its socket. Every private, channel-scoped DISPATCH
frame is recorded into that session's replay buffer, and RESUME replays every
frame with ``s`` greater than the client's last sequence. Today the only gate
re-applied on replay is the intent mask (:func:`_replay_event_allowed`); there
is NO re-check that the resuming principal can still VIEW the channel or is
still a DM participant.

That is a real cross-user leak: a user who was in a channel when a private
MESSAGE_CREATE (or typing / reaction / read-receipt) was queued for them, then
lost access while disconnected (kicked from the server, channel view revoked,
removed from the DM), resumes their still-valid session and receives the other
users' private content they can no longer view.

The fix records the authoritative routing scope on each replayed frame at
dispatch time and fails closed on RESUME: every scoped frame is re-checked
against the *current* membership/visibility and dropped when the viewer no
longer passes (or when the authoritative source is unavailable).
"""

import pytest
from unittest.mock import Mock, patch

from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent


USER_ID = 42
SERVER_ID = 7
CHANNEL_ID = 8
DM_CHANNEL_ID = 99


def _connection(session_manager, mock_websocket):
    conn = Connection(
        websocket=mock_websocket,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)
    return conn


def _sent_types(connection):
    return [call.args[0].get("t") for call in connection.websocket.send_json.call_args_list]


def _message_event(content, *, server_id, channel_id):
    return Event(
        event_type=EventType.MESSAGE_CREATE,
        data={
            "id": "500",
            "channel_id": str(channel_id),
            "server_id": str(server_id) if server_id is not None else None,
            "author_id": "7",
            "content": content,
        },
        server_id=server_id,
        channel_id=channel_id,
    )


@pytest.mark.asyncio
async def test_resume_replay_withholds_channel_event_after_access_loss(
    session_manager, mock_websocket
):
    """A queued private message must not replay after the viewer lost access."""
    intents = int(GatewayIntent.GUILD_MESSAGES | GatewayIntent.DIRECT_MESSAGES)
    old = _connection(session_manager, mock_websocket)
    session = session_manager.create_session(old, USER_ID, intents)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    # Dispatch while the user can still view the channel: the frame is recorded
    # into the session replay buffer by the normal dispatch path.
    await dispatcher.dispatch_event(
        _message_event("top secret", server_id=SERVER_ID, channel_id=CHANNEL_ID),
        [USER_ID],
    )
    recorded = session_manager.get_replay_events(session.session_id, 0)
    assert any(e.get("t") == "MESSAGE_CREATE" for e in recorded), recorded

    # While disconnected, the viewer loses access to the channel.
    servers = Mock()
    servers.get_channel = Mock(return_value=None)
    with patch("src.api.get_servers", return_value=servers), patch(
        "src.api.get_messaging", return_value=None
    ):
        resumed = _connection(session_manager, mock_websocket)
        assert session_manager.resume_session(resumed, session.session_id, 0) is not None
        resumed.websocket.send_json.reset_mock()
        await dispatcher.replay_events(resumed, 0)

    # The private MESSAGE_CREATE must NOT be replayed to a viewer who can no
    # longer see the channel, and the private content must not cross the wire.
    assert "MESSAGE_CREATE" not in _sent_types(resumed)
    wire = [str(call.args[0]) for call in resumed.websocket.send_json.call_args_list]
    assert all("top secret" not in frame for frame in wire)


@pytest.mark.asyncio
async def test_resume_replay_keeps_channel_event_when_access_retained(
    session_manager, mock_websocket
):
    """Non-vacuity: a still-authorized frame must still replay (no blanket deny)."""
    intents = int(GatewayIntent.GUILD_MESSAGES | GatewayIntent.DIRECT_MESSAGES)
    old = _connection(session_manager, mock_websocket)
    session = session_manager.create_session(old, USER_ID, intents)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    await dispatcher.dispatch_event(
        _message_event("still visible", server_id=SERVER_ID, channel_id=CHANNEL_ID),
        [USER_ID],
    )

    servers = Mock()
    servers.get_channel = Mock(return_value=Mock(id=CHANNEL_ID))
    with patch("src.api.get_servers", return_value=servers), patch(
        "src.api.get_messaging", return_value=None
    ):
        resumed = _connection(session_manager, mock_websocket)
        assert session_manager.resume_session(resumed, session.session_id, 0) is not None
        resumed.websocket.send_json.reset_mock()
        await dispatcher.replay_events(resumed, 0)

    assert "MESSAGE_CREATE" in _sent_types(resumed)


@pytest.mark.asyncio
async def test_resume_replay_withholds_dm_event_after_participant_removal(
    session_manager, mock_websocket
):
    """A queued DM message must not replay after the user left the conversation."""
    intents = int(GatewayIntent.DIRECT_MESSAGES)
    old = _connection(session_manager, mock_websocket)
    session = session_manager.create_session(old, USER_ID, intents)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    await dispatcher.dispatch_event(
        _message_event("dm secret", server_id=None, channel_id=DM_CHANNEL_ID),
        [USER_ID],
    )

    messaging = Mock()
    messaging.is_participant = Mock(return_value=False)
    with patch("src.api.get_servers", return_value=None), patch(
        "src.api.get_messaging", return_value=messaging
    ):
        resumed = _connection(session_manager, mock_websocket)
        assert session_manager.resume_session(resumed, session.session_id, 0) is not None
        resumed.websocket.send_json.reset_mock()
        await dispatcher.replay_events(resumed, 0)

    assert "MESSAGE_CREATE" not in _sent_types(resumed)
