"""Authz regression tests at the gateway boundary.

Three attack scenarios that must stay denied across refactors:

* a permission revoked *after* a subscription was granted,
* an artifact owned by a different server than the caller can access,
* a WebSocket handshake carrying a cross-site (forged) Origin.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api.websocket.gateway as gw
import src.api.websocket.handlers.artifacts as ha
import utils.config as config
from src.api.websocket.artifacts import ArtifactSubscriptionRegistry
from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager
from src.core.servers.exceptions import PermissionDeniedError


def _conn():
    return SimpleNamespace(
        is_authenticated=True,
        user_id=42,
        connection_id="authz-regression-conn",
        is_selftest=False,
        websocket=MagicMock(),
    )


def _artifact(server_id=7):
    return SimpleNamespace(
        artifact_type=SimpleNamespace(),
        server_id=server_id,
        conversation_id=None,
        author_id=99,
        rev=1,
        payload={},
    )


def _wire(monkeypatch, permitted_servers):
    allow = {"value": True}

    def _require(user_id, server_id, permission):
        if not allow["value"] or int(server_id) not in permitted_servers:
            raise PermissionDeniedError("denied")
        return True

    servers = MagicMock()
    servers.require_permission.side_effect = _require
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: _artifact(server_id=7))
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)
    monkeypatch.setattr(ha.api, "get_servers", lambda: servers)
    monkeypatch.setattr(ha.api, "get_messaging", lambda: None)

    sync = AsyncMock(return_value=True)
    relay = AsyncMock(return_value=0)
    ack = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "send_artifact_sync", sync)
    monkeypatch.setattr(ha, "relay_artifact_op", relay)
    monkeypatch.setattr(ha, "send_artifact_ack", ack)
    monkeypatch.setattr(ha, "send_artifact_op_rejected", AsyncMock(return_value=True))

    handler = OpcodeHandler(
        session_manager=SessionManager(),
        dispatcher=SimpleNamespace(_rate_limit_per_minute=None),
    )
    handler._artifact_handler._registry = ArtifactSubscriptionRegistry()
    return handler, allow, relay


@pytest.mark.asyncio
async def test_revoked_permission_is_enforced_on_next_op(monkeypatch):
    """A subscription granted while authorized dies when permission is revoked."""
    handler, allow, relay = _wire(monkeypatch, permitted_servers={7})
    conn = _conn()
    registry = handler._artifact_handler._registry

    await handler.handle(conn, GatewayOpcode.ARTIFACT_SUBSCRIBE, {"artifact_id": 10})
    assert registry.get_connection_subscriptions(conn.connection_id) == {10}

    allow["value"] = False
    await handler.handle(
        conn,
        GatewayOpcode.ARTIFACT_OP,
        {"artifact_id": 10, "op": {"op_type": "edit"}},
    )
    assert registry.get_connection_subscriptions(conn.connection_id) == set()
    relay.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_server_artifact_is_denied(monkeypatch):
    """A server-A caller cannot subscribe to or op on a server-B artifact."""
    handler, _allow, relay = _wire(monkeypatch, permitted_servers={8})
    conn = _conn()
    registry = handler._artifact_handler._registry

    await handler.handle(conn, GatewayOpcode.ARTIFACT_SUBSCRIBE, {"artifact_id": 10})
    await handler.handle(
        conn,
        GatewayOpcode.ARTIFACT_OP,
        {"artifact_id": 10, "op": {"op_type": "edit"}},
    )

    assert registry.get_subscribers(10) == set()
    relay.assert_not_awaited()


@pytest.mark.asyncio
async def test_forged_origin_handshake_is_rejected(monkeypatch):
    """A cross-site Origin never obtains an accepted gateway socket."""
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            {"cors_origins": ["https://app.plexichat.com"]}
            if key == "api"
            else ({} if key == "websocket" else default)
        ),
    )
    monkeypatch.setattr(
        "src.api.is_self_test_request", lambda request: False, raising=False
    )

    class _WS:
        def __init__(self):
            self.headers = {"origin": "https://evil.example"}
            self.query_params = {}
            self.client = SimpleNamespace(host="203.0.113.9")
            self.accepted = False
            self.closed = None

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000, reason=""):
            self.closed = (code, reason)

    ws = _WS()
    await gw.gateway_endpoint(ws)
    assert ws.accepted is False
    assert ws.closed is not None
    assert ws.closed[0] == 1008
