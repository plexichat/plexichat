"""Regression: disable_2fa revokes other sessions and closes their sockets.

Disabling 2FA revokes every session except the current one, so previously
trusted sessions cannot bypass the now-disabled second factor. Those revoked
sessions' gateway sockets used to stay authenticated server-side. The close is
targeted: the revoked session's socket closes, the surviving current session's
socket stays open.
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


def test_disable_2fa_closes_revoked_sessions_socket_not_current(
    auth_manager, test_user, monkeypatch
):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    setup = mgr.setup_2fa(uid)
    totp = pyotp.TOTP(setup.secret)
    mgr.confirm_2fa(uid, totp.now())

    sx = mgr._create_session(uid, None, None, None)
    sy = mgr._create_session(uid, None, None, None)  # the surviving session
    cx = _fake_conn(uid, sx.id, "conn-revoked")
    cy = _fake_conn(uid, sy.id, "conn-current")
    _install_dispatcher(monkeypatch, [cx, cy])

    with patch.object(encryption, "verify_password", return_value=True):
        ok = _drive_sync(
            lambda: mgr.disable_2fa(
                uid, "TestPass123!", totp.now(), except_session_id=sy.id
            )
        )
    assert ok is True

    row = mgr._db.fetch_one("SELECT revoked FROM auth_sessions WHERE id = ?", (sx.id,))
    assert row and row["revoked"], "other sessions must be revoked on 2FA disable"
    assert cx.websocket.closed, "revoked session's socket stayed connected"
    code, _reason = cx.websocket.closed[0]
    assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)
    assert cx.disconnecting and cx.disconnected
    assert len(cx.websocket.closed) == 1, "close must be non-vacuous"
    assert not cy.websocket.closed, "current session's socket must stay open"
