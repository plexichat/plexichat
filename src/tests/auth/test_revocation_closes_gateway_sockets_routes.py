"""Regression: auth routes and 2FA enable must revoke live gateway sockets.

The gateway authenticates a socket only at IDENTIFY/RESUME, so a revoked auth
session leaves a live, authenticated socket behind. A cookie clear or a
SECURITY_ALERT frame is a client-side hint, not server-side revocation.

This covers the full-account revocation routes (``/auth/logout`` and
``/auth/sessions/revoke-all`` with no exception) and the 2FA credential
transition. It also pins the deliberate decision to leave single-session
revoke (``DELETE /auth/sessions/{id}``) alone: connections record only a
user id and a *gateway* session id, not the auth-session id, so a blanket
close there would kill the user's other, still-valid devices.
"""

import asyncio
from types import SimpleNamespace

import pyotp
import pytest
from fastapi import Response

from src.api.routes.auth import sessions as sessions_routes
from src.api.websocket.dispatcher import GatewayCloseCode, GatewayDispatcher

pytestmark = [pytest.mark.security, pytest.mark.unit]

UID_A = 515001
UID_B = 515002
SID_A = 900001


class _RecordingWS:
    def __init__(self):
        self.closed = []

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))
        return True


class _FakeConnection:
    def __init__(self, user_id):
        self.user_id = int(user_id)
        self.connection_id = f"conn-{self.user_id}"
        self.websocket = _RecordingWS()
        self.disconnecting = False
        self.disconnected = False

    async def flush_outbound(self):
        return None

    def set_disconnecting(self):
        self.disconnecting = True

    def set_disconnected(self):
        self.disconnected = True


class _FakeSessionManager:
    def __init__(self, by_user):
        self._by_user = by_user

    def get_user_connections(self, user_id):
        return list(self._by_user.get(int(user_id), []))


def _install_dispatcher(monkeypatch, by_user):
    dispatcher = GatewayDispatcher(_FakeSessionManager(by_user))
    import src.api.websocket as ws

    monkeypatch.setattr(ws, "is_setup", lambda: True)
    monkeypatch.setattr(ws, "get_dispatcher", lambda: dispatcher)
    return dispatcher


def _drive(fn):
    """Run a sync revocation on a live loop, then let its scheduled close run."""

    async def _main():
        result = fn()
        await asyncio.sleep(0.01)
        return result

    return asyncio.run(_main())


def _assert_non_resumable_close(conn):
    assert conn.websocket.closed, "revoked socket stayed connected server-side"
    assert conn.disconnecting and conn.disconnected
    code, _reason = conn.websocket.closed[0]
    assert code not in (None, 1000, 4003), f"non-resumable close required, got {code}"
    assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)


def test_logout_closes_only_the_logged_out_users_socket(monkeypatch):
    conn_a, conn_b = _FakeConnection(UID_A), _FakeConnection(UID_B)
    _install_dispatcher(monkeypatch, {UID_A: [conn_a], UID_B: [conn_b]})

    class _Auth:
        def revoke_session(self, uid, sid):
            assert (uid, sid) == (UID_A, SID_A)
            return True

    monkeypatch.setattr(sessions_routes.api, "get_auth", lambda: _Auth())
    principal = SimpleNamespace(user_id=UID_A, session_id=SID_A, token_type="user")

    _drive(lambda: sessions_routes.logout(Response(), principal))

    _assert_non_resumable_close(conn_a)
    assert not conn_b.websocket.closed, "another user's live socket must not close"


def test_revoke_all_closes_all_sessions_when_no_session_is_excepted(monkeypatch):
    conn_a, conn_b = _FakeConnection(UID_A), _FakeConnection(UID_B)
    _install_dispatcher(monkeypatch, {UID_A: [conn_a], UID_B: [conn_b]})

    class _Auth:
        def get_sessions(self, uid):
            return [SimpleNamespace(id=SID_A)]

        def revoke_sessions_bulk(self, uid, ids, except_session_id=None):
            assert uid == UID_A and except_session_id is None
            return len(ids)

    monkeypatch.setattr(sessions_routes.api, "get_auth", lambda: _Auth())
    principal = SimpleNamespace(user_id=UID_A, session_id=SID_A, token_type="user")
    body = SimpleNamespace(except_current=False)

    async def _main():
        await sessions_routes.revoke_all_sessions(body, Response(), principal)
        # revoke_all_sessions is async: yield so the scheduled close runs on
        # the same loop before asyncio.run tears it down.
        await asyncio.sleep(0.01)

    asyncio.run(_main())

    _assert_non_resumable_close(conn_a)
    assert not conn_b.websocket.closed, "another user's live socket must not close"


def test_single_session_revoke_does_not_close_other_devices(monkeypatch):
    """No auth-session id on the connection: blanket close would be wrong."""
    device_a, device_b = _FakeConnection(UID_A), _FakeConnection(UID_A)
    device_b.connection_id = "conn-device-b"
    _install_dispatcher(monkeypatch, {UID_A: [device_a, device_b]})

    class _Auth:
        def revoke_session(self, uid, sid):
            return True

    monkeypatch.setattr(sessions_routes.api, "get_auth", lambda: _Auth())
    principal = SimpleNamespace(user_id=UID_A, session_id=SID_A, token_type="user")

    async def _main():
        await sessions_routes.revoke_session(SID_A, principal)
        await asyncio.sleep(0.01)

    asyncio.run(_main())

    assert not device_a.websocket.closed and not device_b.websocket.closed, (
        "targeted revoke must not kill sockets of other, still-valid devices"
    )


def test_confirm_2fa_closes_gateway_socket(monkeypatch, auth_manager):
    user = auth_manager.register(
        username="confirm2fa_sock_close",
        email="confirm2fa_sock_close@example.com",
        password="TestPass123!",
    )
    uid = int(user.id)
    conn = _FakeConnection(uid)
    _install_dispatcher(monkeypatch, {uid: [conn]})

    secret = auth_manager.setup_2fa(uid).secret
    _drive(lambda: auth_manager.confirm_2fa(uid, pyotp.TOTP(secret).now()))

    _assert_non_resumable_close(conn)
