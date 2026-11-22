"""Regression: disabling an admin account must revoke its live gateway socket.

``POST <admin-base>/users/{id}/toggle-status`` sets ``account_locked`` and
deletes the admin session rows, but -- unlike ``lock-user`` / ``force-logout``
-- it neither pushed a ``SECURITY_LOGOUT`` hint nor closed the target's live
WebSocket on the server. Disabling an account is a revocation; a disabled admin
must not keep a live, authenticated gateway connection.

These tests drive the real handler with a real dispatcher holding one live
connection for the target and assert the socket is actually closed. Before the
fix the connection stayed open (the assert fails), so the test is non-vacuous.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request

import src.api as api
from src.api.routes.admin.users import admin_users as au
from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.admin import permissions as perm_core

pytestmark = [pytest.mark.security, pytest.mark.unit]

TARGET_ID = 2
ACTOR_ID = 1


class _RecordingWS:
    def __init__(self):
        self.closed = []

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))
        return True


class _FakeConnection:
    def __init__(self, user_id):
        self.user_id = user_id
        self.connection_id = "conn-target"
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

    def get_connections_for_users(self, user_ids):
        return [c for uid in user_ids for c in self._by_user.get(uid, [])]


class _Cursor:
    rowcount = 1


class _DB:
    """Hierarchy DB double: target is a subordinate admin, lock state as given."""

    def __init__(self, account_locked=0):
        self.account_locked = account_locked

    def fetch_one(self, sql, params=()):
        return {
            "target_exists": True,
            "current_pos": 100,
            "target_pos": 50,
            "account_locked": self.account_locked,
        }

    def execute(self, sql, params=()):
        return _Cursor()


class _Router(au.AdminUsersMixin, AdminUsersRouterBase):
    pass


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _patch(monkeypatch, db, dispatcher):
    monkeypatch.setattr(au, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(au, "require_admin_permission", lambda request, perm: ACTOR_ID)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(perm_core, "log_admin_action", lambda *a, **k: True)
    monkeypatch.setattr(au, "invalidate_privilege_caches", lambda: None)

    import src.api.websocket as ws

    monkeypatch.setattr(ws, "get_dispatcher", lambda: dispatcher)
    monkeypatch.setattr(ws, "is_setup", lambda: True)


def _run(dispatcher):
    asyncio.run(_Router().toggle_admin_user_status(_request(), str(TARGET_ID)))


def test_disabling_account_closes_live_gateway_socket_server_side(monkeypatch):
    """account_locked 0 -> 1 (disable): socket must be closed, hint delivered."""
    conn = _FakeConnection(TARGET_ID)
    dispatcher = GatewayDispatcher(_FakeSessionManager({TARGET_ID: [conn]}))

    dispatched = []

    async def _record_dispatch(event, user_ids):
        dispatched.append((event.event_type.value, list(user_ids)))
        return 1

    # Only the close is under test; record the client hint instead of sending it.
    monkeypatch.setattr(dispatcher, "dispatch_event", _record_dispatch)
    _patch(monkeypatch, _DB(account_locked=0), dispatcher)

    _run(dispatcher)

    assert conn.websocket.closed, (
        "disabling an admin left its gateway socket connected; a "
        "SECURITY_LOGOUT frame is a client-side hint, not server-side revocation"
    )
    assert conn.disconnecting and conn.disconnected, (
        "connection state must be marked so it is dropped and cannot be resumed"
    )
    code, _reason = conn.websocket.closed[0]
    assert code not in (None, 1000, 4003), f"non-resumable close required, got {code}"
    # The hint must still be delivered (fix must not just drop it).
    assert ("SECURITY_LOGOUT", [TARGET_ID]) in dispatched


def test_enabling_account_does_not_close_gateway_socket(monkeypatch):
    """Non-vacuity: the close is scoped to the disable path only."""
    conn = _FakeConnection(TARGET_ID)
    dispatcher = GatewayDispatcher(_FakeSessionManager({TARGET_ID: [conn]}))

    dispatched = []

    async def _record_dispatch(event, user_ids):
        dispatched.append((event.event_type.value, list(user_ids)))
        return 1

    monkeypatch.setattr(dispatcher, "dispatch_event", _record_dispatch)
    _patch(monkeypatch, _DB(account_locked=1), dispatcher)

    _run(dispatcher)

    assert not conn.websocket.closed, "enabling an account must not close sockets"
    assert not dispatched, "enabling an account must not send a logout hint"
