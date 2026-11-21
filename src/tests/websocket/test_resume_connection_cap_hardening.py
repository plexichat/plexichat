"""RESUME must obey the same per-user connection cap as IDENTIFY.

The gateway caps concurrent connections per user (``max_connections_per_user``).
IDENTIFY enforces it, but a resumable session outlives its socket: a client can
churn IDENTIFY/disconnect to accumulate sessions, then RESUME every one of them
on a fresh socket. ``resume_session`` used to add each of those sockets to the
user's connection set with no cap check, so the limit was trivially bypassable
and a single authenticated user could hold unbounded live gateway sockets.

A resume that MOVES a session onto a replacement socket (its previous socket is
still live) does not grow the set and must remain allowed, otherwise a normal
reconnect at the cap would be broken.
"""

import pytest

from src.api.websocket.connection import Connection
from src.api.websocket.opcodes import GatewayOpcode


USER_ID = 12345


def _new_connection(session_manager, mock_websocket):
    conn = Connection(
        websocket=mock_websocket,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)
    return conn


@pytest.mark.asyncio
async def test_resume_cannot_exceed_per_user_connection_cap(
    opcode_handler, session_manager, mock_websocket, sample_identify_payload
):
    # Accumulate 6 sessions whose sockets are already gone. Sessions survive
    # remove_connection() so they remain resumable.
    orphan_sessions = []
    for _ in range(6):
        conn = _new_connection(session_manager, mock_websocket)
        _, _, close = await opcode_handler.handle(
            conn, GatewayOpcode.IDENTIFY, sample_identify_payload
        )
        assert close is None
        orphan_sessions.append(conn.session_id)
        session_manager.remove_connection(conn.connection_id)

    # Fill the cap with 5 live connections / fresh sessions.
    for _ in range(5):
        conn = _new_connection(session_manager, mock_websocket)
        _, _, close = await opcode_handler.handle(
            conn, GatewayOpcode.IDENTIFY, sample_identify_payload
        )
        assert close is None
    assert session_manager.can_user_connect(USER_ID) is False

    # A 6th socket tries to resume one of the orphaned sessions. The old socket
    # is gone, so this would allocate a brand-new counted connection past the
    # cap. It must be denied.
    attacker = _new_connection(session_manager, mock_websocket)
    op, data, close = await opcode_handler.handle(
        attacker,
        GatewayOpcode.RESUME,
        {
            "token": sample_identify_payload["token"],
            "session_id": orphan_sessions[0],
            "seq": 0,
        },
    )
    assert op == GatewayOpcode.INVALID_SESSION
    assert data["d"] is False
    assert close is None
    assert attacker.is_authenticated is False


@pytest.mark.asyncio
async def test_resume_replacing_live_connection_at_cap_still_allowed(
    opcode_handler, session_manager, mock_websocket, sample_identify_payload
):
    """A reconnect that replaces a still-live socket must not be blocked."""
    live = []
    for _ in range(5):
        conn = _new_connection(session_manager, mock_websocket)
        await opcode_handler.handle(
            conn, GatewayOpcode.IDENTIFY, sample_identify_payload
        )
        live.append(conn)
    assert session_manager.can_user_connect(USER_ID) is False

    replacement = _new_connection(session_manager, mock_websocket)
    op, data, close = await opcode_handler.handle(
        replacement,
        GatewayOpcode.RESUME,
        {
            "token": sample_identify_payload["token"],
            "session_id": live[0].session_id,
            "seq": 0,
        },
    )
    assert op == GatewayOpcode.DISPATCH
    assert data["t"] == "RESUMED"
    assert close is None
    assert replacement.is_authenticated is True


def test_resume_session_directly_enforces_cap_for_orphan(
    session_manager, mock_websocket
):
    """Direct SessionManager call must also refuse to grow past the cap."""
    orphans = []
    for _ in range(6):
        conn = _new_connection(session_manager, mock_websocket)
        session = session_manager.create_session(conn, USER_ID, 513)
        orphans.append(session.session_id)
        session_manager.remove_connection(conn.connection_id)

    for _ in range(5):
        conn = _new_connection(session_manager, mock_websocket)
        session_manager.create_session(conn, USER_ID, 513)

    assert session_manager.can_user_connect(USER_ID) is False
    assert (
        session_manager.resume_session(
            _new_connection(session_manager, mock_websocket), orphans[0], 0
        )
        is None
    )
