"""Regression: single-session revoke closes only that auth session's socket.

The gateway socket's ``Connection.session_id`` is a gateway replay id, not the
auth-session id. ``ConnectionHandler._verify_token`` now also binds the verified
``TokenInfo.session_id`` onto the connection as ``auth_session_id`` at
IDENTIFY/RESUME, so revoking one auth session can close just that session's
sockets. Revoking session X must leave a sibling session Y's socket open.
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.api.websocket.dispatcher import GatewayCloseCode, GatewayDispatcher
from src.api.websocket.handlers.connection import ConnectionHandler
from src.core.auth.models import AccountType, TokenInfo

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
    """Run a sync revoke on a live loop, then let its scheduled close finish."""

    async def _main():
        result = fn()
        await asyncio.sleep(0.01)
        return result

    return asyncio.run(_main())


def test_revoking_session_x_closes_only_x_socket(monkeypatch, auth_manager, test_user):
    """Revoking session X closes X's socket but leaves session Y's socket open."""
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    session_x = mgr._create_session(uid, None, None, None)
    session_y = mgr._create_session(uid, None, None, None)
    conn_x = _fake_conn(uid, session_x.id, "conn-session-x")
    conn_y = _fake_conn(uid, session_y.id, "conn-session-y")
    _install_dispatcher(monkeypatch, [conn_x, conn_y])

    revoked = _drive_sync(lambda: auth_manager.revoke_session(uid, session_x.id))
    assert revoked is True, "the target auth session must actually be revoked"

    assert conn_x.websocket.closed, "revoked session's socket stayed connected"
    code, _reason = conn_x.websocket.closed[0]
    assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)
    assert conn_x.disconnecting and conn_x.disconnected
    assert len(conn_x.websocket.closed) == 1, "close must be non-vacuous"

    assert not conn_y.websocket.closed, (
        "a sibling, still-valid session's socket must not be closed"
    )


def test_verify_token_binds_auth_session_id_and_keeps_bot_principal():
    """Binding is sourced from TokenInfo.session_id without breaking bot rebind."""

    def _info(user_id, session_id, token_type="user", account_id=None):
        return TokenInfo(
            valid=True,
            token_type=token_type,
            account_id=account_id if account_id is not None else user_id,
            user_id=user_id,
            session_id=session_id,
            permissions={},
            rate_limit_tier="standard",
            expires_at=None,
            username="u",
            account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
        )

    class _Auth:
        def __init__(self, info):
            self.info = info

        def verify_token(self, *args, **kwargs):
            return self.info

    user_conn = SimpleNamespace(auth_session_id=None)
    handler = ConnectionHandler(None, auth_module=_Auth(_info(100, 777)))
    principal = asyncio.run(handler._verify_token("tok", bind_connection=user_conn))
    assert principal == 100
    assert user_conn.auth_session_id == 777

    # Bot verdicts still rebind to the bot account; they carry no auth session.
    bot_conn = SimpleNamespace(auth_session_id=None)
    handler = ConnectionHandler(None, auth_module=_Auth(_info(200, None, "bot", 300)))
    principal = asyncio.run(handler._verify_token("bot", bind_connection=bot_conn))
    assert principal == 300
    assert bot_conn.auth_session_id is None
