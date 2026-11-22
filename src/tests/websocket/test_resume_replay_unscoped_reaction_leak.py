"""RESUME replay must re-check private events whose producer omits Event scope.

N648 stamped ``__gateway_authz_scope`` using ``Event.server_id`` /
``Event.channel_id`` in ``_build_dispatch_payload``. That leaves a real gap:
several production routes build their private Event WITHOUT those routing
fields and put the channel/conversation id only in ``d``. The reaction route
(``src/api/routes/reactions.py::_dispatch_reaction_event``) and the read-receipt
routes are exactly that shape::

    Event(event_type=MESSAGE_REACTION_ADD,
          data={"channel_id": str(channel_id), "message_id": ..., ...})
    await dispatcher.dispatch_event(event, recipient_ids)

Such a frame is recorded into the replay buffer with NO scope marker, so the
RESUME re-check in ``GatewayDispatcher.replay_events`` sees ``_MISSING`` and
replays it unconditionally. A participant who reacted in a DM/channel, then
lost access while disconnected, still receives the queued private reaction
frame on resume. This test proves that leak and that the fix closes it.
"""

import pytest
from unittest.mock import Mock, patch

from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.session import REPLAY_SCOPE_KEY
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent


USER_ID = 42
DM_CONVERSATION_ID = 99


def _connection(session_manager, mock_websocket):
    conn = Connection(
        websocket=mock_websocket,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)
    return conn


def _sent_frames(connection):
    return [call.args[0] for call in connection.websocket.send_json.call_args_list]


def _reaction_event_like_production_route():
    """The exact Event shape built by ``_dispatch_reaction_event``: private
    conversation metadata lives in ``d``; Event routing fields are unset.
    """
    return Event(
        event_type=EventType.MESSAGE_REACTION_ADD,
        data={
            "user_id": "7",
            "message_id": "500",
            "channel_id": str(DM_CONVERSATION_ID),
            "emoji": "🔥",
        },
    )


@pytest.mark.asyncio
async def test_production_shaped_reaction_event_is_stamped_and_rechecked(
    session_manager, mock_websocket
):
    """A reaction frame must carry a server-stamped scope and be withheld on
    RESUME after the viewer loses DM participation.
    """
    intents = int(GatewayIntent.all_intents())
    old = _connection(session_manager, mock_websocket)
    session = session_manager.create_session(old, USER_ID, intents)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    await dispatcher.dispatch_event(_reaction_event_like_production_route(), [USER_ID])

    # The frame is recorded by the live dispatch path. Its scope MUST have
    # been stamped server-side from the private channel id in ``d``; otherwise
    # RESUME treats it as scope-less and replays it unconditionally.
    recorded = session_manager.get_replay_events(session.session_id, 0)
    reaction_frames = [e for e in recorded if e.get("t") == "MESSAGE_REACTION_ADD"]
    assert reaction_frames, recorded
    assert REPLAY_SCOPE_KEY in reaction_frames[0], (
        "private reaction frame was recorded without a replay authz scope: "
        f"{reaction_frames[0]!r}"
    )
    assert reaction_frames[0][REPLAY_SCOPE_KEY][1] == DM_CONVERSATION_ID

    # While disconnected the viewer is removed from the conversation, so the
    # authoritative DM participation gate now fails.
    messaging = Mock()
    messaging.is_participant = Mock(return_value=False)
    with (
        patch("src.api.get_servers", return_value=None),
        patch("src.api.get_messaging", return_value=messaging),
    ):
        resumed = _connection(session_manager, mock_websocket)
        assert (
            session_manager.resume_session(resumed, session.session_id, 0) is not None
        )
        resumed.websocket.send_json.reset_mock()
        await dispatcher.replay_events(resumed, 0)

    frames = _sent_frames(resumed)
    assert not any(f.get("t") == "MESSAGE_REACTION_ADD" for f in frames), frames
    assert all("🔥" not in str(f) for f in frames), frames


@pytest.mark.asyncio
async def test_production_shaped_reaction_event_replays_when_access_retained(
    session_manager, mock_websocket
):
    """Non-vacuity: a still-participating viewer must still get the frame."""
    intents = int(GatewayIntent.all_intents())
    old = _connection(session_manager, mock_websocket)
    session = session_manager.create_session(old, USER_ID, intents)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    await dispatcher.dispatch_event(_reaction_event_like_production_route(), [USER_ID])

    messaging = Mock()
    messaging.is_participant = Mock(return_value=True)
    with (
        patch("src.api.get_servers", return_value=None),
        patch("src.api.get_messaging", return_value=messaging),
    ):
        resumed = _connection(session_manager, mock_websocket)
        assert (
            session_manager.resume_session(resumed, session.session_id, 0) is not None
        )
        resumed.websocket.send_json.reset_mock()
        await dispatcher.replay_events(resumed, 0)

    frames = _sent_frames(resumed)
    assert any(f.get("t") == "MESSAGE_REACTION_ADD" for f in frames), frames


@pytest.mark.asyncio
async def test_production_shaped_ack_event_is_stamped_and_rechecked(
    session_manager, mock_websocket
):
    """The read-receipt route builds the same unscoped shape; cover it too.

    ``src/api/routes/messages/read_receipts.py`` creates a MESSAGE_ACK Event
    whose channel id lives only in ``d``. The central scope derivation must
    stamp it, and RESUME must withhold it after DM participant removal.
    """
    intents = int(GatewayIntent.all_intents())
    old = _connection(session_manager, mock_websocket)
    session = session_manager.create_session(old, USER_ID, intents)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    ack = Event(
        event_type=EventType.MESSAGE_ACK,
        data={
            "channel_id": str(DM_CONVERSATION_ID),
            "user_id": "7",
            "message_id": "500",
        },
    )
    await dispatcher.dispatch_event(ack, [USER_ID])

    recorded = session_manager.get_replay_events(session.session_id, 0)
    ack_frames = [e for e in recorded if e.get("t") == "MESSAGE_ACK"]
    assert ack_frames, recorded
    assert REPLAY_SCOPE_KEY in ack_frames[0], ack_frames[0]
    assert ack_frames[0][REPLAY_SCOPE_KEY][1] == DM_CONVERSATION_ID

    messaging = Mock()
    messaging.is_participant = Mock(return_value=False)
    with (
        patch("src.api.get_servers", return_value=None),
        patch("src.api.get_messaging", return_value=messaging),
    ):
        resumed = _connection(session_manager, mock_websocket)
        assert (
            session_manager.resume_session(resumed, session.session_id, 0) is not None
        )
        resumed.websocket.send_json.reset_mock()
        await dispatcher.replay_events(resumed, 0)

    frames = _sent_frames(resumed)
    assert not any(f.get("t") == "MESSAGE_ACK" for f in frames), frames
