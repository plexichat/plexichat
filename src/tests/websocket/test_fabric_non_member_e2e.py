"""End-to-end gateway authorization against a real server and real modules.

The unit suite (``test_opcode_manager_authorization.py``) mocks the owning
manager to refuse. These tests instead stand up the real module graph -- a real
database, the real ``ServerManager`` membership graph, a real server-scoped
artifact and a real ``SumManager`` workbook -- then drive the gateway dispatch
path (``OpcodeHandler.handle``) as a genuinely non-member client and assert the
fabric opcodes deny it.

Only the transport edges are doubled: the SFU/relay senders and the gateway
dispatcher, so a denial can be observed as "nothing was sent or relayed". The
authorization decision itself runs through the production code.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api as api
import src.core.suite.sum as sum_domain
import src.api.websocket.handlers.artifacts as ha
import src.api.websocket.handlers.sum as hs
from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.core.artifacts.models import Artifact, ArtifactStatus, ArtifactType
from src.core.artifacts.repository import create_artifact
from src.core.base import generate_id
from src.core.events.types import GatewayIntent


def _now_ms() -> int:
    return int(time.time() * 1000)


def _connect(session_manager, websocket, user_id: int) -> Connection:
    """Create a READY gateway connection for ``user_id``."""
    connection = Connection(
        websocket=websocket,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(connection)
    session = session_manager.create_session(
        connection, user_id=user_id, intents=GatewayIntent.all_intents()
    )
    connection.set_identified(user_id, session.session_id, GatewayIntent.all_intents())
    return connection


@pytest.fixture
def fabric(
    db,
    server_manager,
    messaging_manager,
    auth_manager,
    two_users,
    tmp_path,
    monkeypatch,
    session_manager,
    mock_auth_module,
    mock_websocket,
):
    """A real server with an owner (member) and an outsider (non-member)."""
    owner, outsider = two_users

    server = server_manager.create_server(owner.id, "Fabric E2E server")

    manager = sum_domain.setup(
        db,
        servers_module=server_manager,
        storage_dir=str(tmp_path / "sum"),
    )
    workbook = manager.create_workbook(
        owner.id, "Server workbook", scope="server", server_id=server.id
    )
    sheet_id = int(workbook["sheets"][0]["id"])

    now = _now_ms()
    artifact = Artifact(
        id=generate_id(),
        conversation_id=None,
        channel_id=None,
        server_id=server.id,
        author_id=owner.id,
        artifact_type=ArtifactType.WHITEBOARD,
        title="Server board",
        summary=None,
        status=ArtifactStatus.LIVE,
        recorded=False,
        has_transcript=False,
        payload={"content": "initial"},
        created_at=now,
        updated_at=now,
    )
    create_artifact(db, artifact)

    # The module graph the handlers resolve through. Real managers, so the
    # membership decision is the production one.
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_servers", lambda: server_manager)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging_manager)
    monkeypatch.setattr(api, "get_auth", lambda: auth_manager)

    # Capability gating is orthogonal to membership; keep it out of the way.
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)

    dispatcher = MagicMock()
    dispatcher.dispatch_to_connection = AsyncMock(return_value=True)
    dispatcher._rate_limit_per_minute = None
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher)

    artifact_sync = AsyncMock(return_value=True)
    artifact_relay = AsyncMock(return_value=1)
    sum_sync = AsyncMock(return_value=True)
    sum_relay = AsyncMock(return_value=1)
    monkeypatch.setattr(ha, "send_artifact_sync", artifact_sync)
    monkeypatch.setattr(ha, "relay_artifact_op", artifact_relay)
    monkeypatch.setattr(hs, "send_sum_sync", sum_sync)
    monkeypatch.setattr(hs, "relay_sum_op", sum_relay)

    handler = OpcodeHandler(
        session_manager=session_manager,
        auth_module=mock_auth_module,
        servers_module=server_manager,
        dispatcher=GatewayDispatcher(session_manager=session_manager),
    )

    try:
        yield SimpleNamespace(
            owner=owner,
            outsider=outsider,
            server=server,
            manager=manager,
            workbook=workbook,
            sheet_id=sheet_id,
            artifact=artifact,
            handler=handler,
            dispatcher=dispatcher,
            artifact_sync=artifact_sync,
            artifact_relay=artifact_relay,
            sum_sync=sum_sync,
            sum_relay=sum_relay,
            owner_conn=_connect(session_manager, mock_websocket, owner.id),
            outsider_conn=_connect(session_manager, mock_websocket, outsider.id),
            db=db,
        )
    finally:
        sum_domain._manager = None


def _artifact_ops_count(db, artifact_id: int) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS n FROM artifact_ops WHERE artifact_id = ?",
        (artifact_id,),
    )
    return int(row["n"]) if row else 0


# === Non-member: the fabric denials ========================================


@pytest.mark.asyncio
async def test_artifact_subscribe_denied_for_non_member(fabric):
    result = await fabric.handler.handle(
        fabric.outsider_conn,
        GatewayOpcode.ARTIFACT_SUBSCRIBE,
        {"artifact_id": int(fabric.artifact.id)},
    )

    assert result == (None, None, None)
    fabric.artifact_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_artifact_op_denied_for_non_member(fabric):
    result = await fabric.handler.handle(
        fabric.outsider_conn,
        GatewayOpcode.ARTIFACT_OP,
        {
            "artifact_id": int(fabric.artifact.id),
            "op": {"op_type": "edit", "content": "tampered"},
        },
    )

    assert result == (None, None, None)
    fabric.artifact_relay.assert_not_awaited()
    assert _artifact_ops_count(fabric.db, int(fabric.artifact.id)) == 0


@pytest.mark.asyncio
async def test_sum_subscribe_denied_for_non_member(fabric):
    result = await fabric.handler.handle(
        fabric.outsider_conn,
        GatewayOpcode.SUM_SUBSCRIBE,
        {"workbook_id": int(fabric.workbook["id"])},
    )

    assert result == (None, None, None)
    fabric.sum_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_sum_op_denied_and_workbook_unchanged(fabric):
    result = await fabric.handler.handle(
        fabric.outsider_conn,
        GatewayOpcode.SUM_OP,
        {
            "workbook_id": int(fabric.workbook["id"]),
            "op": {
                "operation_id": "outsider-op-1",
                "operation_type": "set_cell",
                "sheet_id": fabric.sheet_id,
                "base_revision": 0,
                "cells": [
                    {"row": 0, "column": 0, "value_type": "text", "value": "pwn"}
                ],
            },
        },
    )

    assert result == (None, None, None)
    fabric.sum_relay.assert_not_awaited()
    after = fabric.manager.get_workbook(fabric.owner.id, int(fabric.workbook["id"]))
    assert after["current_revision"] == 0


@pytest.mark.asyncio
async def test_guild_members_denied_for_non_member(fabric, server_manager, monkeypatch):
    # The gate must stop before the member listing: reaching it would raise and
    # be reported as internal_error instead of the dedicated denial.
    listing = MagicMock(side_effect=AssertionError("member listing must not run"))
    monkeypatch.setattr(server_manager, "get_members", listing)

    result = await fabric.handler.handle(
        fabric.outsider_conn,
        GatewayOpcode.REQUEST_GUILD_MEMBERS,
        {"guild_id": int(fabric.server.id)},
    )

    assert result == (None, None, None)
    listing.assert_not_called()
    assert fabric.dispatcher.dispatch_to_connection.await_count == 1
    event = fabric.dispatcher.dispatch_to_connection.await_args.args[1]
    assert event.data["code"] == "guild_members_denied"


# === Member: the same opcodes are allowed ==================================


@pytest.mark.asyncio
async def test_member_can_subscribe_to_artifact_and_workbook(fabric):
    await fabric.handler.handle(
        fabric.owner_conn,
        GatewayOpcode.ARTIFACT_SUBSCRIBE,
        {"artifact_id": int(fabric.artifact.id)},
    )
    await fabric.handler.handle(
        fabric.owner_conn,
        GatewayOpcode.SUM_SUBSCRIBE,
        {"workbook_id": int(fabric.workbook["id"])},
    )

    fabric.artifact_sync.assert_awaited()
    fabric.sum_sync.assert_awaited()


@pytest.mark.asyncio
async def test_member_can_write_artifact_and_workbook(fabric):
    await fabric.handler.handle(
        fabric.owner_conn,
        GatewayOpcode.ARTIFACT_OP,
        {
            "artifact_id": int(fabric.artifact.id),
            "op": {"op_type": "edit", "content": "owner edit"},
        },
    )
    await fabric.handler.handle(
        fabric.owner_conn,
        GatewayOpcode.SUM_OP,
        {
            "workbook_id": int(fabric.workbook["id"]),
            "op": {
                "operation_id": "owner-op-1",
                "operation_type": "set_cell",
                "sheet_id": fabric.sheet_id,
                "base_revision": 0,
                "cells": [{"row": 0, "column": 0, "value_type": "text", "value": "ok"}],
            },
        },
    )

    fabric.artifact_relay.assert_awaited()
    fabric.sum_relay.assert_awaited()
    assert _artifact_ops_count(fabric.db, int(fabric.artifact.id)) == 1


@pytest.mark.asyncio
async def test_member_can_request_guild_members(fabric):
    await fabric.handler.handle(
        fabric.owner_conn,
        GatewayOpcode.REQUEST_GUILD_MEMBERS,
        {"guild_id": int(fabric.server.id)},
    )

    assert fabric.dispatcher.dispatch_to_connection.await_count == 1
    event = fabric.dispatcher.dispatch_to_connection.await_args.args[1]
    assert event.data.get("chunk_count") == 1
