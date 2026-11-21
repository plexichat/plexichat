"""Regression: admin force-logout must revoke the target's live gateway sockets.

``POST /admin/security/force-logout`` revoked every DB session for the target
and pushed a ``SECURITY_LOGOUT`` frame, but -- unlike the site-wide
``/security/logout-all`` route, which calls ``close_all_connections()`` -- it
never closed that user's active WebSocket connections on the server. The
frame is only a *client-side* hint: the socket stays authenticated and live
server-side after the logout, so a logged-out (or banned/disabled) user keeps
a working, privileged gateway connection.

This test drives the real handler with a real dispatcher whose session manager
holds one live connection for the target, and asserts the socket is actually
closed. Before the fix the connection is merely sent a frame and stays live.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request

import src.api as api
import src.core.admin as admin_core
import src.core.auth as auth_core
from src.api.routes.admin import security
from src.api.websocket.dispatcher import GatewayDispatcher

pytestmark = [pytest.mark.security, pytest.mark.unit]

TARGET_ID = 424242
ACTOR_ID = 10


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


class _HierarchyDB:
    """Ordinary-user target: no admin hierarchy gate applies."""

    row = {
        "target_exists": False,
        "current_pos": 0,
        "target_pos": 0,
        "account_locked": 0,
    }

    def fetch_one(self, sql, params=()):
        return self.row

    def execute(self, sql, params=()):
        return SimpleNamespace(rowcount=1)


def _request() -> Request:
    return cast(Request, SimpleNamespace(client=None))


def _patch_common(monkeypatch, dispatcher):
    monkeypatch.setattr(security, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        security, "require_admin_permission", lambda request, perm: ACTOR_ID
    )
    monkeypatch.setattr(api, "get_db", lambda: _HierarchyDB())
    monkeypatch.setattr(auth_core, "logout_all", lambda uid: 1)

    import src.api.websocket as ws

    monkeypatch.setattr(ws, "get_dispatcher", lambda: dispatcher)
    monkeypatch.setattr(ws, "is_setup", lambda: True)
    monkeypatch.setattr(
        "src.utils.system_alerts.record_system_alert", lambda *a, **k: None
    )


def test_force_logout_closes_target_gateway_socket_server_side(monkeypatch):
    conn = _FakeConnection(TARGET_ID)
    dispatcher = GatewayDispatcher(_FakeSessionManager({TARGET_ID: [conn]}))

    dispatched = []

    async def _record_dispatch(event, user_ids):
        dispatched.append((event.event_type.value, list(user_ids)))
        return 1

    # The client hint is not under test here; only the server-side close is.
    monkeypatch.setattr(dispatcher, "dispatch_event", _record_dispatch)
    _patch_common(monkeypatch, dispatcher)

    asyncio.run(
        security.force_logout(_request(), SimpleNamespace(user_id=TARGET_ID))
    )

    assert conn.websocket.closed, (
        "force-logout left the target's gateway socket connected; sending a "
        "SECURITY_LOGOUT frame is a client-side hint, not server-side revocation"
    )
    assert conn.disconnecting and conn.disconnected, (
        "connection state must be marked so it is dropped and cannot be resumed"
    )
    # Fail closed on resume: a non-resumable, non-1000 close code.
    code, _reason = conn.websocket.closed[0]
    assert code not in (None, 1000, 4003), f"non-resumable close required, got {code}"
    # The client hint must still be delivered (fix must not just drop it).
    assert ("SECURITY_LOGOUT", [TARGET_ID]) in dispatched


def test_lock_user_closes_target_gateway_socket_server_side(monkeypatch):
    """A ban/disable path must not leave a live authenticated socket either."""
    conn = _FakeConnection(TARGET_ID)
    dispatcher = GatewayDispatcher(_FakeSessionManager({TARGET_ID: [conn]}))

    async def _record_dispatch(event, user_ids):
        return 1

    monkeypatch.setattr(dispatcher, "dispatch_event", _record_dispatch)
    _patch_common(monkeypatch, dispatcher)
    monkeypatch.setattr(admin_core, "lock_user", lambda uid, duration: None)

    asyncio.run(
        security.admin_lock_user(
            _request(), SimpleNamespace(user_id=TARGET_ID, duration_seconds=60)
        )
    )

    assert conn.websocket.closed, (
        "locking an account left its gateway socket connected server-side"
    )
    assert conn.disconnected
