"""N813 follow-up: cross-worker revocation must close sibling-worker sockets.

Before the fix, ``dispatch_remote_event`` only fanned a ``SECURITY_ALERT`` /
``SECURITY_LOGOUT`` out to local connections as a *client-side hint*. The
socket itself stayed authenticated and usable on the worker that received the
event, so a revoked/banned user kept a live server-side session on every
worker except the one that handled the revocation.

These tests model that deployment with two connections on the *receiving*
dispatcher (the "sibling worker") and fail on the unfixed code: the revoked
socket is never closed. They pass only when the receiver closes the local
sockets server-side, without closing a still-valid sibling session or a
different user's socket.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.opcodes import GatewayCloseCode

pytestmark = [pytest.mark.security, pytest.mark.unit, pytest.mark.asyncio]


def _identified(session_manager, user_id, auth_session_id):
    ws = MagicMock()
    ws.client = SimpleNamespace(host="127.0.0.1")
    ws.headers = {}
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    conn = Connection(
        websocket=ws,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)
    session_manager.create_session(conn, user_id=user_id, intents=0)
    # Bound from the verified TokenInfo.session_id at IDENTIFY (see
    # handlers/connection.py:362). This is what a session-scoped revoke matches.
    conn.auth_session_id = auth_session_id
    return conn, ws


def _alert(user_id, session_ids=None, action="session_revoked", revoke=True):
    detail = {"revoke": revoke}
    if session_ids is not None:
        detail["session_ids"] = [str(s) for s in session_ids]
    return {
        "user_id": str(user_id),
        "action": action,
        "detail": detail,
    }


async def test_remote_session_revocation_closes_sibling_socket(session_manager):
    """A revoked session's socket on a sibling worker is closed server-side."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, revoked_ws = _identified(session_manager, 42, 101)
    _, sibling_ws = _identified(session_manager, 42, 202)
    _, other_ws = _identified(session_manager, 43, 303)

    sent = await dispatcher.dispatch_remote_event(
        "SECURITY_ALERT", _alert(42, session_ids=[101]), [42]
    )

    revoked_ws.close.assert_awaited_once()
    assert revoked_ws.close.await_args.kwargs["code"] == (
        GatewayCloseCode.AUTHENTICATION_FAILED
    )
    # The still-valid sibling session and another user must stay open.
    sibling_ws.close.assert_not_awaited()
    other_ws.close.assert_not_awaited()
    assert sent >= 0  # the close is the guarantee; the hint is optional


async def test_remote_security_logout_closes_all_user_sockets(session_manager):
    """SECURITY_LOGOUT closes every one of the target's local sockets."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, ws_a = _identified(session_manager, 42, 101)
    _, ws_b = _identified(session_manager, 42, 202)
    _, other_ws = _identified(session_manager, 43, 303)

    await dispatcher.dispatch_remote_event(
        "SECURITY_LOGOUT", {"user_id": "42", "message": "Security logout"}, [42]
    )

    ws_a.close.assert_awaited_once()
    ws_b.close.assert_awaited_once()
    other_ws.close.assert_not_awaited()


async def test_informational_security_alert_does_not_close_sockets(session_manager):
    """A non-revocation alert stays a client hint; normal dispatch continues."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, ws = _identified(session_manager, 42, 101)

    sent = await dispatcher.dispatch_remote_event(
        "SECURITY_ALERT",
        _alert(42, action="two_factor_enabled", revoke=False),
        [42],
    )

    ws.close.assert_not_awaited()
    assert sent == 1
    ws.send_json.assert_awaited_once()
    assert ws.send_json.await_args.args[0]["t"] == "SECURITY_ALERT"


async def test_revocation_without_subject_id_cannot_close_anything(session_manager):
    """A malformed payload (no d.user_id) must not close a bystander socket."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, ws = _identified(session_manager, 42, 101)

    await dispatcher.dispatch_remote_event(
        "SECURITY_ALERT",
        {"action": "session_revoked", "detail": {"revoke": True}},
        [42],
    )

    ws.close.assert_not_awaited()


async def test_replay_cannot_close_a_newer_session(session_manager):
    """Session-id targeting makes an old revoke harmless after re-login.

    Session ids are never reused, so a replayed event for session 101 cannot
    match the socket bound to the newly created session 909. The close itself
    is idempotent; a second delivery of the same event must not raise.
    """
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, revoked_ws = _identified(session_manager, 42, 101)

    event = _alert(42, session_ids=[101])
    await dispatcher.dispatch_remote_event("SECURITY_ALERT", event, [42])
    await dispatcher.dispatch_remote_event("SECURITY_ALERT", event, [42])
    revoked_ws.close.assert_awaited()

    _, fresh_ws = _identified(session_manager, 42, 909)
    await dispatcher.dispatch_remote_event("SECURITY_ALERT", event, [42])
    fresh_ws.close.assert_not_awaited()
