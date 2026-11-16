"""Deep hardening: artifact/server scope is enforced on every artifact opcode.

The artifact's stored row is the sole authority for its server/conversation
scope. A caller that can access server A must not be able to subscribe to,
unsubscribe from, or write ops against an artifact owned by server B, and a
client-supplied scope claim that disagrees with the row is rejected.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api.websocket.handlers.artifacts as ha
from src.api.websocket.artifacts import ArtifactSubscriptionRegistry
from src.core.servers.exceptions import PermissionDeniedError


def _conn(user_id=42, connection_id="scope-conn"):
    return SimpleNamespace(
        is_authenticated=True,
        user_id=user_id,
        connection_id=connection_id,
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


def _wire(monkeypatch, artifact, permitted_servers):
    """Permission is granted only for ``permitted_servers``."""

    def _require_permission(user_id, server_id, permission):
        if int(server_id) not in permitted_servers:
            raise PermissionDeniedError("not a member / missing permission")
        return True

    servers = MagicMock()
    servers.require_permission.side_effect = _require_permission
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)
    monkeypatch.setattr(ha.api, "get_servers", lambda: servers)
    monkeypatch.setattr(ha.api, "get_messaging", lambda: None)


# === ARTIFACT_SUBSCRIBE (60) ================================================


@pytest.mark.asyncio
async def test_artifact_scope_enforced_on_subscribe(monkeypatch):
    """An artifact on server B is not subscribable by a server-A-only user."""
    _wire(monkeypatch, _artifact(server_id=7), permitted_servers={8})
    registry = ArtifactSubscriptionRegistry()
    handler = ha.ArtifactHandler()
    handler._registry = registry
    sync = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "send_artifact_sync", sync)

    conn = _conn()
    await handler.handle_artifact_subscribe(
        conn, {"artifact_id": 10}, SimpleNamespace()
    )

    assert registry.get_subscribers(10) == set()
    assert registry.get_connection_subscriptions(conn.connection_id) == set()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscribe_rejects_conflicting_scope_claim(monkeypatch):
    """Even a permitted viewer cannot claim a different server scope."""
    _wire(monkeypatch, _artifact(server_id=7), permitted_servers={7})
    registry = ArtifactSubscriptionRegistry()
    handler = ha.ArtifactHandler()
    handler._registry = registry
    monkeypatch.setattr(ha, "send_artifact_sync", AsyncMock(return_value=True))

    await handler.handle_artifact_subscribe(
        _conn(),
        {"artifact_id": 10, "server_id": 8},
        SimpleNamespace(),
    )

    assert registry.get_subscribers(10) == set()


@pytest.mark.asyncio
async def test_subscribe_accepts_matching_scope_claim(monkeypatch):
    """Complement: a claim equal to the stored server scope is accepted."""
    _wire(monkeypatch, _artifact(server_id=7), permitted_servers={7})
    registry = ArtifactSubscriptionRegistry()
    handler = ha.ArtifactHandler()
    handler._registry = registry
    monkeypatch.setattr(ha, "send_artifact_sync", AsyncMock(return_value=True))

    conn = _conn()
    await handler.handle_artifact_subscribe(
        conn, {"artifact_id": 10, "guild_id": 7}, SimpleNamespace()
    )

    assert registry.get_subscribers(10) == {conn.user_id}


# === ARTIFACT_UNSUBSCRIBE (61) ==============================================


@pytest.mark.asyncio
async def test_artifact_scope_enforced_on_unsubscribe(monkeypatch):
    """A conflicting scope claim must not drive an unsubscribe off-scope."""
    _wire(monkeypatch, _artifact(server_id=7), permitted_servers={7})
    registry = ArtifactSubscriptionRegistry()
    handler = ha.ArtifactHandler()
    handler._registry = registry
    conn = _conn()
    registry.subscribe(conn.user_id, 10, conn.connection_id)
    assert registry.get_subscribers(10) == {conn.user_id}

    await handler.handle_artifact_unsubscribe(conn, {"artifact_id": 10, "server_id": 8})

    # The conflicting claim is rejected; the legitimate subscription stays.
    assert registry.get_connection_subscriptions(conn.connection_id) == {10}


@pytest.mark.asyncio
async def test_unsubscribe_without_claims_still_cleans_up(monkeypatch):
    """Complement: an ordinary (claim-free) unsubscribe still removes self."""
    _wire(monkeypatch, _artifact(server_id=7), permitted_servers={7})
    registry = ArtifactSubscriptionRegistry()
    handler = ha.ArtifactHandler()
    handler._registry = registry
    conn = _conn()
    registry.subscribe(conn.user_id, 10, conn.connection_id)

    await handler.handle_artifact_unsubscribe(conn, {"artifact_id": 10})

    assert registry.get_connection_subscriptions(conn.connection_id) == set()


# === ARTIFACT_OP (62) =======================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"artifact_id": 10, "op": {"op_type": "edit"}},  # no access to server 7
        {"artifact_id": 10, "server_id": 8, "op": {"op_type": "edit"}},
    ],
    ids=["cross-server-artifact", "conflicting-scope-claim"],
)
async def test_artifact_scope_enforced_on_op(monkeypatch, payload):
    """A cross-server op is neither persisted nor relayed."""
    _wire(monkeypatch, _artifact(server_id=7), permitted_servers={8})
    handler = ha.ArtifactHandler()
    handler._registry = ArtifactSubscriptionRegistry()
    relay = AsyncMock(return_value=0)
    ack = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "relay_artifact_op", relay)
    monkeypatch.setattr(ha, "send_artifact_ack", ack)
    monkeypatch.setattr(ha, "send_artifact_op_rejected", AsyncMock(return_value=True))

    result = await handler.handle_artifact_op(
        _conn(), payload, SimpleNamespace(_rate_limit_per_minute=None)
    )

    assert result == (None, None, None)
    relay.assert_not_awaited()
    ack.assert_not_awaited()
