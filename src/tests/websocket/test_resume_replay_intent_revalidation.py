"""RESUME replay must re-apply the intent gate with the *sanitized* mask.

A resumable gateway session outlives its socket and may be hydrated from a
persisted (Valkey) payload that was recorded while the session still held
privileged intents. RESUME strips those bits before handing the mask to the
new connection (see ``_safe_resume_intents``), but the replay buffer is
restored verbatim. Without a second gate at replay time, a stale session
replays the privileged frames and regains event visibility that IDENTIFY
would deny -- bypassing the resume-intent hardening entirely.

This test targets the ``replay_events`` field, not the intent mask itself.
"""

import pytest

from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.events.types import GatewayIntent


USER_ID = 42


def _connection(sm, mock_websocket):
    conn = Connection(
        websocket=mock_websocket,
        connection_id=sm.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    sm.add_connection(conn)
    return conn


@pytest.mark.asyncio
async def test_resume_replay_drops_frames_requiring_stripped_intents(
    session_manager, mock_websocket
):
    """A privileged frame from a stale buffer must not be replayed on RESUME."""
    old = _connection(session_manager, mock_websocket)
    stale_intents = (
        int(GatewayIntent.GUILDS)
        | int(GatewayIntent.GUILD_MESSAGES)
        | int(GatewayIntent.GUILD_MEMBERS)
    )
    session = session_manager.create_session(old, USER_ID, stale_intents)

    # Both frames were recorded while the session still had GUILD_MEMBERS.
    # Only GUILD_MEMBER_ADD actually requires that privileged bit.
    session_manager.record_event(
        session.session_id,
        {
            "op": 0,
            "t": "GUILD_MEMBER_ADD",
            "s": 1,
            "d": {"server_id": "7", "user": {"id": "9"}},
        },
    )
    session_manager.record_event(
        session.session_id,
        {
            "op": 0,
            "t": "MESSAGE_CREATE",
            "s": 2,
            "d": {"server_id": "7", "channel_id": "8", "content": "hello"},
        },
    )

    resumed = _connection(session_manager, mock_websocket)
    assert session_manager.resume_session(resumed, session.session_id, 0) is not None

    # The connection mask was sanitized: GUILD_MEMBERS is gone.
    assert not resumed.intents & int(GatewayIntent.GUILD_MEMBERS)
    assert resumed.intents & int(GatewayIntent.GUILD_MESSAGES)

    dispatcher = GatewayDispatcher(session_manager=session_manager)
    count = await dispatcher.replay_events(resumed, 0)

    sent_types = [
        call.args[0].get("t") for call in resumed.websocket.send_json.call_args_list
    ]
    # The privileged event must NOT be replayed; the ordinary one still is.
    assert "GUILD_MEMBER_ADD" not in sent_types
    assert "MESSAGE_CREATE" in sent_types
    assert count == 1


@pytest.mark.asyncio
async def test_resume_replay_keeps_frames_allowed_by_current_mask(
    session_manager, mock_websocket
):
    """A non-privileged frame still replays -- the gate is not blanket denial."""
    old = _connection(session_manager, mock_websocket)
    session = session_manager.create_session(old, USER_ID, int(GatewayIntent.GUILDS))
    session_manager.record_event(
        session.session_id,
        {"op": 0, "t": "GUILD_CREATE", "s": 1, "d": {"id": "7"}},
    )

    resumed = _connection(session_manager, mock_websocket)
    assert session_manager.resume_session(resumed, session.session_id, 0) is not None
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    count = await dispatcher.replay_events(resumed, 0)

    assert count == 1
    assert (
        resumed.websocket.send_json.call_args_list[0].args[0].get("t") == "GUILD_CREATE"
    )
