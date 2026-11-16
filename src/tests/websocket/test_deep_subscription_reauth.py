"""Deep hardening: live subscriptions are re-authorized on every fabric op.

A subscribe-time permission check is only a point-in-time decision. These
tests drive the real registry plus mocked permission authorities to prove a
role/grant revoked *after* subscribing is dropped on the next opcode, and that
all three artifact / all three Sum opcodes perform the re-check.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api.websocket.handlers.artifacts as ha
import src.api.websocket.handlers.sum as hs
from src.api.websocket.artifacts import ArtifactSubscriptionRegistry
from src.api.websocket.sum import SumSubscriptionRegistry
from src.core.servers.exceptions import PermissionDeniedError
from src.core.suite.sum.manager import SumPermissionError


def _conn(user_id=42, connection_id="reauth-conn"):
    return SimpleNamespace(
        is_authenticated=True,
        user_id=user_id,
        connection_id=connection_id,
        is_selftest=False,
        websocket=MagicMock(),
    )


def _artifact():
    return SimpleNamespace(
        artifact_type=SimpleNamespace(),
        server_id=7,
        conversation_id=None,
        author_id=99,
        rev=1,
        payload={},
    )


# === Artifacts: revoked permission prunes the live subscription =============


@pytest.mark.asyncio
async def test_artifact_revoked_permission_prunes_subscription_on_op(monkeypatch):
    """Subscribe while permitted, revoke, then op -> subscription is gone."""
    registry = ArtifactSubscriptionRegistry()
    handler = ha.ArtifactHandler()
    handler._registry = registry

    allow = {"value": True}

    def _require_permission(user_id, server_id, permission):
        if not allow["value"]:
            raise PermissionDeniedError("revoked")
        return True

    servers = MagicMock()
    servers.require_permission.side_effect = _require_permission
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: _artifact())
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)
    monkeypatch.setattr(ha.api, "get_servers", lambda: servers)
    monkeypatch.setattr(ha.api, "get_messaging", lambda: None)

    sync = AsyncMock(return_value=True)
    relay = AsyncMock(return_value=0)
    ack = AsyncMock(return_value=True)
    rejected = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "send_artifact_sync", sync)
    monkeypatch.setattr(ha, "relay_artifact_op", relay)
    monkeypatch.setattr(ha, "send_artifact_ack", ack)
    monkeypatch.setattr(ha, "send_artifact_op_rejected", rejected)

    conn = _conn()

    await handler.handle_artifact_subscribe(
        conn, {"artifact_id": 10}, SimpleNamespace()
    )
    assert registry.get_connection_subscriptions(conn.connection_id) == {10}
    assert registry.get_subscribers(10) == {conn.user_id}

    # Permission is revoked while the socket stays open.
    allow["value"] = False

    await handler.handle_artifact_op(
        conn,
        {"artifact_id": 10, "op": {"op_type": "edit"}},
        SimpleNamespace(_rate_limit_per_minute=None),
    )

    assert registry.get_connection_subscriptions(conn.connection_id) == set()
    assert registry.get_subscribers(10) == set()
    relay.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("opcode", ["subscribe", "unsubscribe", "op"])
async def test_artifact_every_opcode_reauthorizes(monkeypatch, opcode):
    handler = ha.ArtifactHandler()
    handler.reauthorize_subscriptions = MagicMock(return_value=[])
    conn = _conn()

    monkeypatch.setattr(ha, "send_artifact_sync", AsyncMock(return_value=True))
    if opcode == "subscribe":
        await handler.handle_artifact_subscribe(conn, {}, SimpleNamespace())
    elif opcode == "unsubscribe":
        await handler.handle_artifact_unsubscribe(conn, {})
    else:
        await handler.handle_artifact_op(conn, {}, SimpleNamespace())
    handler.reauthorize_subscriptions.assert_called_once_with(conn)


# === Sum: revoked grant prunes the live subscription =======================


@pytest.mark.asyncio
async def test_sum_revoked_grant_prunes_subscription_on_op(monkeypatch):
    registry = SumSubscriptionRegistry()
    handler = hs.SumHandler()
    handler._registry = registry

    allow = {"value": True}

    def _get_workbook(user_id, workbook_id):
        if not allow["value"]:
            raise SumPermissionError("revoked")
        return {"current_revision": 0}

    def _apply_operation(*a, **k):
        if not allow["value"]:
            raise SumPermissionError("revoked")
        return {"revision": 1}

    manager = MagicMock()
    manager.get_workbook.side_effect = _get_workbook
    manager.replay_operations.return_value = []
    manager.apply_operation.side_effect = _apply_operation

    monkeypatch.setattr(hs, "_capability_allows_sum", lambda: True)
    monkeypatch.setattr(hs, "_get_sum_manager", lambda: manager)
    sync = AsyncMock(return_value=True)
    relay = AsyncMock(return_value=0)
    monkeypatch.setattr(hs, "send_sum_sync", sync)
    monkeypatch.setattr(hs, "relay_sum_op", relay)

    conn = _conn()
    await handler.handle_sum_subscribe(conn, {"workbook_id": 9}, SimpleNamespace())
    assert registry.get_connection_subscriptions(conn.connection_id) == {9}
    assert registry.get_subscribers(9) == {conn.user_id}

    allow["value"] = False
    await handler.handle_sum_op(
        conn,
        {
            "workbook_id": 9,
            "op": {
                "operation_id": "op-1",
                "operation_type": "set_cell",
                "sheet_id": 1,
                "base_revision": 0,
                "cells": [{"row": 0, "col": 0, "value": "x"}],
            },
        },
        SimpleNamespace(_rate_limit_per_minute=None),
    )

    assert registry.get_connection_subscriptions(conn.connection_id) == set()
    assert registry.get_subscribers(9) == set()
    relay.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("opcode", ["subscribe", "unsubscribe", "op"])
async def test_sum_every_opcode_reauthorizes(monkeypatch, opcode):
    handler = hs.SumHandler()
    handler.reauthorize_subscriptions = MagicMock(return_value=[])
    conn = _conn()
    if opcode == "subscribe":
        await handler.handle_sum_subscribe(conn, {}, SimpleNamespace())
    elif opcode == "unsubscribe":
        await handler.handle_sum_unsubscribe(conn, {})
    else:
        await handler.handle_sum_op(conn, {}, SimpleNamespace())
    handler.reauthorize_subscriptions.assert_called_once_with(conn)
