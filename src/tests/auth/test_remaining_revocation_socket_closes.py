"""Regression: remaining single/multi-session revoke paths close their sockets.

Covers the session-lifecycle revocations that previously left a live gateway
socket authenticated after the underlying auth session was revoked:

* ``AuthManager.logout`` (bearer-token self-logout);
* the max-sessions-per-user eviction performed on login;
* ``revoke_sessions_bulk`` (including the ``except_session_id`` survivor).

Each close must be targeted: the revoked session's socket closes, a sibling
session's socket stays open. No blanket user-wide close is used.
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.api.websocket.dispatcher import GatewayCloseCode, GatewayDispatcher

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


def _assert_targeted_close(closed_conn, open_conn):
    assert closed_conn.websocket.closed, "revoked session's socket stayed connected"
    code, _reason = closed_conn.websocket.closed[0]
    assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)
    assert closed_conn.disconnecting and closed_conn.disconnected
    assert len(closed_conn.websocket.closed) == 1, "close must be non-vacuous"
    assert not open_conn.websocket.closed, "sibling session's socket must stay open"


def test_bearer_logout_closes_only_that_sessions_socket(
    auth_manager, test_user, monkeypatch
):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    sx = mgr._create_session(uid, None, None, None)
    sy = mgr._create_session(uid, None, None, None)
    cx = _fake_conn(uid, sx.id, "conn-x")
    cy = _fake_conn(uid, sy.id, "conn-y")
    _install_dispatcher(monkeypatch, [cx, cy])

    assert _drive_sync(lambda: mgr.logout(sx.token)) is True
    row = mgr._db.fetch_one("SELECT revoked FROM auth_sessions WHERE id = ?", (sx.id,))
    assert row and row["revoked"]
    _assert_targeted_close(cx, cy)


def test_max_session_eviction_closes_evicted_sessions_socket(
    auth_manager, test_user, monkeypatch
):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    monkeypatch.setattr(
        mgr,
        "_config",
        {
            **mgr._config,
            "sessions": {**mgr._config.get("sessions", {}), "max_per_user": 1},
        },
    )
    first = mgr._create_session(uid, None, None, None)
    c_first = _fake_conn(uid, first.id, "conn-evicted")
    c_second = _fake_conn(uid, None, "conn-kept")
    _install_dispatcher(monkeypatch, [c_first, c_second])

    # Creating a second session evicts the oldest (the first one). Do it on a
    # live loop so the fix's scheduled targeted close can run, then bind the
    # surviving socket to the newly created session id.
    async def _main():
        second = mgr._create_session(uid, None, None, None)
        c_second.auth_session_id = second.id
        await asyncio.sleep(0.01)
        return second

    asyncio.run(_main())

    row = mgr._db.fetch_one(
        "SELECT revoked FROM auth_sessions WHERE id = ?", (first.id,)
    )
    assert row and row["revoked"], "oldest session must be evicted"
    _assert_targeted_close(c_first, c_second)


def test_bulk_revoke_except_current_keeps_survivor_socket_open(
    auth_manager, test_user, monkeypatch
):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    sx = mgr._create_session(uid, None, None, None)
    sy = mgr._create_session(uid, None, None, None)  # the surviving session
    cx = _fake_conn(uid, sx.id, "conn-revoked")
    cy = _fake_conn(uid, sy.id, "conn-survivor")
    _install_dispatcher(monkeypatch, [cx, cy])

    revoked = _drive_sync(
        lambda: mgr.revoke_sessions_bulk(uid, [sx.id, sy.id], except_session_id=sy.id)
    )
    assert revoked == 1
    _assert_targeted_close(cx, cy)
