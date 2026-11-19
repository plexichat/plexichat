"""Regression guards for explicit cross-worker scope propagation.

The Valkey pub/sub envelope must carry the authoritative
``Event.server_id``/``Event.channel_id`` in its own ``scope`` field. Payload
builders are NOT required to duplicate those keys inside ``d``, so re-inferring
scope from ``d`` on the receiving worker misclassifies events (guild <-> DM)
and applies the wrong intent gate -- leaking guild payloads to DM-intent-only
clients (and vice versa), or withholding them from the right audience.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api.websocket.__init__ as ws_init
import src.core.database as database_module
from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent

pytestmark = pytest.mark.unit


def _identified(session_manager, user_id, intents):
    ws = MagicMock()
    ws.client = SimpleNamespace(host="127.0.0.1")
    ws.headers = {}
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    conn = Connection(
        websocket=ws,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)
    session_manager.create_session(conn, user_id=user_id, intents=intents)
    return conn, ws


def _valkey_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(database_module, "valkey_available", lambda: True)
    monkeypatch.setattr(database_module, "get_valkey_client", lambda: client)
    return client


def test_publish_envelope_carries_explicit_scope(events_module, monkeypatch):
    """The published envelope carries scope even when ``d`` omits it."""
    em = events_module._manager
    client = _valkey_client(monkeypatch)
    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={"content": "private-dm"},
        server_id=None,
        channel_id=555,
    )

    em._publish_cross_worker(event, [1, 2])

    _channel, raw = client.publish.call_args[0]
    body = json.loads(raw)
    assert body["scope"] == [None, 555]
    # The original client payload is left untouched.
    assert body["d"] == {"content": "private-dm"}
    assert "channel_id" not in body["d"]


@pytest.mark.asyncio
async def test_explicit_dm_scope_withheld_from_guild_only_intent(session_manager):
    """Envelope scope (not ``d``) drives the gate: DM needs DIRECT_MESSAGES."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, guild_ws = _identified(
        session_manager, 111, int(GatewayIntent.GUILD_MESSAGES)
    )
    _, dm_ws = _identified(
        session_manager, 222, int(GatewayIntent.DIRECT_MESSAGES)
    )

    # ``d`` has no scope keys at all; only the envelope says this is a DM.
    sent = await dispatcher.dispatch_remote_event(
        "MESSAGE_CREATE", {"content": "private-dm"}, [111, 222], scope=(None, 555)
    )

    assert sent == 1
    guild_ws.send_json.assert_not_called()
    dm_ws.send_json.assert_called_once()
    assert dm_ws.send_json.call_args.args[0]["d"]["content"] == "private-dm"


@pytest.mark.asyncio
async def test_explicit_guild_scope_withheld_from_dm_only_intent(session_manager):
    """A guild event whose ``d`` lacks guild_id still needs GUILD_MESSAGES."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, guild_ws = _identified(
        session_manager, 333, int(GatewayIntent.GUILD_MESSAGES)
    )
    _, dm_ws = _identified(
        session_manager, 444, int(GatewayIntent.DIRECT_MESSAGES)
    )

    sent = await dispatcher.dispatch_remote_event(
        "MESSAGE_CREATE", {"content": "guild-msg"}, [333, 444], scope=(7, None)
    )

    assert sent == 1
    dm_ws.send_json.assert_not_called()
    guild_ws.send_json.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_payload_without_scope_still_uses_data_fallback(session_manager):
    """A publisher without the envelope field still infers scope from ``d``."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, dm_ws = _identified(
        session_manager, 555, int(GatewayIntent.DIRECT_MESSAGES)
    )

    sent = await dispatcher.dispatch_remote_event(
        "MESSAGE_CREATE", {"channel_id": "999", "content": "dm"}, [555]
    )

    assert sent == 1
    dm_ws.send_json.assert_called_once()


@pytest.mark.asyncio
async def test_receiver_forwards_envelope_scope(monkeypatch):
    """The receive path passes the envelope scope through to the dispatcher."""
    dispatcher = MagicMock()
    dispatcher.dispatch_remote_event = AsyncMock(return_value=1)
    monkeypatch.setattr(ws_init, "_dispatcher", dispatcher)
    monkeypatch.setattr(ws_init, "_get_worker_id", lambda: "local")

    payload = json.dumps(
        {
            "id": "x1",
            "t": "MESSAGE_CREATE",
            "d": {"content": "dm"},
            "u": [1],
            "sw": "other",
            "scope": [None, 555],
        }
    )

    await ws_init._handle_cross_worker_message(payload)

    dispatcher.dispatch_remote_event.assert_awaited_once_with(
        "MESSAGE_CREATE", {"content": "dm"}, [1], scope=[None, 555]
    )
