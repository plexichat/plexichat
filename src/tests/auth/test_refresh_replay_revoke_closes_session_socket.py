"""Regression: refresh-token replay revocation closes only its own socket.

``AuthManager._revoke_for_refresh_replay`` kills a *single* auth session when a
pre-rotation bearer credential is presented twice (a stolen-token signal).
It used to revoke the row, invalidate the verify cache and write an audit event
but never closed the live gateway socket bound to that session: the socket kept
working server-side even though its session was revoked. Because the socket now
records ``auth_session_id`` from the verified token, the revoke can close just
that session's sockets and leave the user's other, still-valid sessions open.
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.api.websocket.dispatcher import GatewayCloseCode
from src.core.auth.replay import refresh_replay_guard

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _RecordingWS:
    def __init__(self):
        self.closed = []

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))
        return True


def _fake_conn(user_id, auth_session_id, name):
    conn = SimpleNamespace(
        user_id=int(user_id),
        auth_session_id=int(auth_session_id) if auth_session_id is not None else None,
        connection_id=name,
        websocket=_RecordingWS(),
        disconnecting=False,
        disconnected=False,
    )

    async def _flush():
        return None

    def _set_disconnecting():
        conn.disconnecting = True

    def _set_disconnected():
        conn.disconnected = True

    conn.flush_outbound = _flush
    conn.set_disconnecting = _set_disconnecting
    conn.set_disconnected = _set_disconnected
    return conn


class _FakeSessionManager:
    def __init__(self, conns):
        self._conns = list(conns)

    def get_all_connections(self):
        return list(self._conns)


def _install_dispatcher(monkeypatch, conns):
    dispatcher = None
    from src.api.websocket.dispatcher import GatewayDispatcher

    dispatcher = GatewayDispatcher(_FakeSessionManager(conns))
    import src.api.websocket as ws

    monkeypatch.setattr(ws, "is_setup", lambda: True)
    monkeypatch.setattr(ws, "get_dispatcher", lambda: dispatcher)
    return dispatcher


def _drive_sync(fn):
    """Run a sync revoke on a live loop, then let its scheduled close finish."""

    async def _main():
        result = fn()
        await asyncio.sleep(0.01)
        return result

    return asyncio.run(_main())


def test_refresh_replay_revoke_closes_only_compromised_sessions_socket(
    monkeypatch, auth_manager, test_user
):
    """Replaying a rotated token kills that session's socket, not a sibling's."""
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    session_x = mgr._create_session(uid, None, None, None)
    session_y = mgr._create_session(uid, None, None, None)
    conn_x = _fake_conn(uid, session_x.id, "conn-session-x")
    conn_y = _fake_conn(uid, session_y.id, "conn-session-y")
    _install_dispatcher(monkeypatch, [conn_x, conn_y])

    # The guard is process-local; clear leftovers from other tests so the two
    # refresh calls below follow the intended rotate-then-replay sequence.
    refresh_replay_guard.clear()

    # First use rotates session X's token and records the pre-rotation bearer
    # as consumed. Reusing that same bearer must be flagged as a replay.
    rotated = _drive_sync(lambda: auth_manager.refresh_session(session_x.token))
    assert rotated and rotated != session_x.token, "first refresh must rotate"
    replayed = _drive_sync(lambda: auth_manager.refresh_session(session_x.token))
    assert replayed is None, "the replayed bearer must be rejected"

    # The compromised session is revoked server-side...
    row_x = mgr._db.fetch_one(
        "SELECT revoked FROM auth_sessions WHERE id = ?", (session_x.id,)
    )
    assert row_x and row_x["revoked"], "replayed session must be revoked"

    # ...and so is the socket that authenticated under it (the gap this pins).
    assert conn_x.websocket.closed, "compromised session's socket stayed connected"
    code, _reason = conn_x.websocket.closed[0]
    assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)
    assert conn_x.disconnecting and conn_x.disconnected
    assert len(conn_x.websocket.closed) == 1, "close must be non-vacuous"

    # A sibling, still-valid session's socket must be left open.
    assert not conn_y.websocket.closed, (
        "targeted revoke must not close another session's socket"
    )
