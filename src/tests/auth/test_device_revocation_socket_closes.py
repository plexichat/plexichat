"""Regression: device revoke / untrust close only that device's session sockets.

``revoke_device`` and ``untrust_device`` revoke every auth session belonging to
one device (or fingerprint). Those sessions' gateway sockets used to stay
authenticated server-side. A blanket user-wide close would be wrong (it would
kill the user's other, still-valid devices), so each revoked session's socket
is closed individually by its auth-session id.
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
    assert closed_conn.websocket.closed, "revoked device's socket stayed connected"
    code, _reason = closed_conn.websocket.closed[0]
    assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)
    assert closed_conn.disconnecting and closed_conn.disconnected
    assert len(closed_conn.websocket.closed) == 1, "close must be non-vacuous"
    assert not open_conn.websocket.closed, "another device's socket must stay open"


def _make_device(mgr, uid, fingerprint, trusted=0):
    did = mgr._generate_id()
    now = mgr._get_timestamp()
    mgr._db.execute(
        "INSERT INTO auth_devices "
        "(id, user_id, fingerprint, trusted, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (did, uid, fingerprint, trusted, now, now),
    )
    return did


def test_revoke_device_closes_its_sessions_socket_only(
    auth_manager, test_user, monkeypatch
):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    dev_a = _make_device(mgr, uid, "fp-A")
    dev_b = _make_device(mgr, uid, "fp-B")
    sx = mgr._create_session(uid, dev_a, None, None)
    sy = mgr._create_session(uid, dev_b, None, None)
    cx = _fake_conn(uid, sx.id, "conn-device-a")
    cy = _fake_conn(uid, sy.id, "conn-device-b")
    _install_dispatcher(monkeypatch, [cx, cy])

    assert _drive_sync(lambda: mgr.revoke_device(uid, dev_a)) is True
    row = mgr._db.fetch_one("SELECT revoked FROM auth_sessions WHERE id = ?", (sx.id,))
    assert row and row["revoked"]
    _assert_targeted_close(cx, cy)


def test_untrust_device_closes_its_sessions_socket_only(
    auth_manager, test_user, monkeypatch
):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    uid = int(test_user.id)
    _make_device(mgr, uid, "fp-A", trusted=1)
    _make_device(mgr, uid, "fp-B", trusted=1)
    sx = mgr._create_session(uid, mgr._db.fetch_one(
        "SELECT id FROM auth_devices WHERE user_id = ? AND fingerprint = ?",
        (uid, "fp-A"),
    )["id"], None, None)
    sy = mgr._create_session(uid, mgr._db.fetch_one(
        "SELECT id FROM auth_devices WHERE user_id = ? AND fingerprint = ?",
        (uid, "fp-B"),
    )["id"], None, None)
    cx = _fake_conn(uid, sx.id, "conn-fp-a")
    cy = _fake_conn(uid, sy.id, "conn-fp-b")
    _install_dispatcher(monkeypatch, [cx, cy])

    assert _drive_sync(lambda: mgr.untrust_device(uid, "fp-A")) is True
    row = mgr._db.fetch_one("SELECT revoked FROM auth_sessions WHERE id = ?", (sx.id,))
    assert row and row["revoked"]
    _assert_targeted_close(cx, cy)
