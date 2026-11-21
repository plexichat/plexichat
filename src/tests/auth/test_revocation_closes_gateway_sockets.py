"""Regression: ordinary auth revocation must close live gateway sockets.

The gateway authenticates a WebSocket only at IDENTIFY/RESUME. A
``SECURITY_ALERT`` / ``SECURITY_LOGOUT`` dispatch is only a *client-side* hint:
until the socket is actually closed it stays authenticated and usable
server-side. ``f3461252`` fixed the admin force-logout/lock paths, but the
regular revocation paths -- mass logout, password change and account purge --
revoked rows and cleared the verify cache while leaving the socket live.

These tests drive the real core functions with a real dispatcher holding one
live connection for the target and assert the socket is actually closed. Before
the fix the connection merely received a frame (or nothing) and stayed live.
"""

import asyncio

import pytest

from src.api.websocket.dispatcher import GatewayCloseCode, GatewayDispatcher
from src.core.auth.deletion_log import DeletionLog
from src.core.auth.reaper import AccountReaper

pytestmark = [pytest.mark.security, pytest.mark.unit]


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


def _install_dispatcher(monkeypatch, conn):
    """Wire a real dispatcher (one live socket) into the gateway module."""
    dispatcher = GatewayDispatcher(_FakeSessionManager({conn.user_id: [conn]}))
    import src.api.websocket as ws

    monkeypatch.setattr(ws, "is_setup", lambda: True)
    monkeypatch.setattr(ws, "get_dispatcher", lambda: dispatcher)
    return dispatcher


def _drive_sync_revocation(fn):
    """Call a sync revocation from a live loop, then let its scheduled close run.

    ``close_user_gateway_connections`` uses ``run_coroutine_threadsafe`` so it
    works from worker threads too; yielding here lets that scheduled coroutine
    complete before the assertions below.
    """

    async def _main():
        result = fn()
        await asyncio.sleep(0.01)
        return result

    return asyncio.run(_main())


def _assert_socket_closed(conn):
    assert conn.websocket.closed, (
        "revocation left a live gateway socket connected server-side; "
        "a client-side SECURITY_ALERT frame is not server-side revocation"
    )
    assert conn.disconnecting and conn.disconnected, (
        "connection state must be marked so it is dropped and cannot be resumed"
    )
    code, _reason = conn.websocket.closed[0]
    assert code not in (None, 1000, 4003), (
        f"a non-resumable close code is required on revocation, got {code}"
    )
    assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)


def test_logout_all_closes_live_gateway_socket(monkeypatch, auth_manager, test_user):
    uid = int(test_user.id)
    conn = _FakeConnection(uid)
    _install_dispatcher(monkeypatch, conn)

    revoked = _drive_sync_revocation(lambda: auth_manager.logout_all(uid))

    assert revoked >= 0
    _assert_socket_closed(conn)
    # The test must actually have exercised a close: a vacuous pass would leave
    # the socket untouched.
    assert len(conn.websocket.closed) == 1


def test_password_change_closes_live_gateway_socket(monkeypatch, auth_manager, test_user):
    uid = int(test_user.id)
    conn = _FakeConnection(uid)
    _install_dispatcher(monkeypatch, conn)

    changed = _drive_sync_revocation(
        lambda: auth_manager.change_password(uid, "TestPass123!", "NewPassword123!")
    )

    assert changed is True
    _assert_socket_closed(conn)


def test_reaper_purge_closes_live_gateway_socket(
    monkeypatch, db, auth_manager, test_user, tmp_path
):
    uid = int(test_user.id)
    conn = _FakeConnection(uid)
    _install_dispatcher(monkeypatch, conn)

    reaper = AccountReaper(db, auth_manager)
    reaper._log = DeletionLog(file_path=str(tmp_path / "deletion_log.jsonl"))
    # Drive the purge on the loop so the scheduled socket close can complete.
    _drive_sync_revocation(lambda: reaper.purge_user(uid, test_user.username))

    _assert_socket_closed(conn)
