"""Regression: 2FA disable with no session exception closes EVERY socket.

c62071ac added server-side gateway closure to ``disable_2fa`` for both of its
revocation branches:

* ``except_session_id is not None`` -- revoke other sessions, keep the current
  one, and close only the revoked sessions' sockets (covered by
  ``test_disable_2fa_revocation_socket_close.py``);
* ``except_session_id is None`` -- revoke *all* sessions, so *all* of their
  sockets must be closed server-side.

Only the first branch had a regression test. Without a test on the second
branch, dropping its ``to_close`` capture (the exact shape of the stale revert
that c62071ac repaired) revokes every row in ``auth_sessions`` while leaving
each already-authenticated gateway socket live: the gateway authenticates a
socket only at IDENTIFY/RESUME, so a DB-only revoke does not evict it.

This test is non-vacuous: removing the fix (no socket close on disable) leaves
all three sockets open and fails the assertions below.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pyotp
import pytest

from src.api.websocket.dispatcher import GatewayCloseCode, GatewayDispatcher
from src.utils import encryption

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
        auth_session_id=int(auth_session_id),
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
    dispatcher = GatewayDispatcher(_FakeSessionManager(conns))
    import src.api.websocket as ws

    monkeypatch.setattr(ws, "is_setup", lambda: True)
    monkeypatch.setattr(ws, "get_dispatcher", lambda: dispatcher)
    return dispatcher


def _drive_sync(fn):
    async def _main():
        result = fn()
        await asyncio.sleep(0.01)
        return result

    return asyncio.run(_main())


def test_disable_2fa_without_exception_closes_every_sessions_socket(
    auth_manager, test_user, monkeypatch
):
    """No exception means a full transition: every session's socket closes."""
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    setup = mgr.setup_2fa(uid)
    totp = pyotp.TOTP(setup.secret)
    mgr.confirm_2fa(uid, totp.now())

    sessions = [mgr._create_session(uid, None, None, None) for _ in range(3)]
    conns = [_fake_conn(uid, s.id, f"conn-{s.id}") for s in sessions]
    _install_dispatcher(monkeypatch, conns)

    # Deliberately omit except_session_id: exercise the revoke-ALL branch.
    with patch.object(encryption, "verify_password", return_value=True):
        ok = _drive_sync(lambda: mgr.disable_2fa(uid, "TestPass123!", totp.now()))
    assert ok is True

    for session in sessions:
        row = mgr._db.fetch_one(
            "SELECT revoked FROM auth_sessions WHERE id = ?", (session.id,)
        )
        assert row and row["revoked"], f"session {session.id} was not revoked"

    for conn in conns:
        assert len(conn.websocket.closed) == 1, (
            f"revoked session's socket {conn.connection_id} stayed connected"
        )
        code, _reason = conn.websocket.closed[0]
        assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)
        assert conn.disconnecting and conn.disconnected
