"""Cached rendering of identical outbound presence payloads."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.intents import ALL_INTENTS
from src.api.websocket.rendering import PayloadRenderCache
from src.api.websocket.session import SessionManager
from src.core.events.models import Event
from src.core.events.types import EventType
from src.core.events.snowflakes import stringify_snowflake_ids


class _FakeSessionManager:
    def __init__(self, connections):
        self._connections = connections

    def get_connections_for_users(self, user_ids):
        return list(self._connections)

    def record_event(self, session_id, event):
        raise AssertionError("fake connections have no session_id")


def _fake_connection(name):
    conn = Connection(websocket=AsyncMock(), connection_id=name, intents=ALL_INTENTS)
    conn.state = ConnectionState.READY
    conn.user_id = 7
    conn.is_selftest = True
    return conn


def test_render_cache_renders_identical_payload_once():
    cache = PayloadRenderCache()
    payload = {"user_id": 123, "status": "online", "guild_id": 456}

    first = cache.render(payload, stringify_snowflake_ids)
    second = cache.render(dict(payload), stringify_snowflake_ids)

    assert first == second == {"user_id": "123", "status": "online", "guild_id": "456"}
    assert first is second  # same cached object, not a rebuilt one
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_render_cache_is_bounded_and_key_order_independent():
    cache = PayloadRenderCache(max_entries=2)
    cache.render({"a": 1, "b": 2}, stringify_snowflake_ids)
    cache.render({"b": 2, "a": 1}, stringify_snowflake_ids)  # same key
    assert cache.stats()["hits"] == 1

    cache.render({"c": 3}, stringify_snowflake_ids)
    cache.render({"d": 4}, stringify_snowflake_ids)
    assert cache.stats()["size"] == 2  # oldest entry evicted


def test_connection_renders_repeated_payload_once(monkeypatch):
    import src.api.websocket.connection as connection_module

    calls = []

    def counting_renderer(data):
        calls.append(data)
        return {"rendered": data["user_id"]}

    monkeypatch.setattr(connection_module, "stringify_snowflake_ids", counting_renderer)
    conn = _fake_connection("c1")

    first = conn.render_dispatch_data({"user_id": 42})
    second = conn.render_dispatch_data({"user_id": 42})

    assert first == second == {"rendered": 42}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_dispatcher_fanout_renders_presence_payload_once(monkeypatch):
    import src.api.websocket.dispatcher as dispatcher_module

    real_renderer = dispatcher_module.stringify_snowflake_ids
    calls = []

    def counting_renderer(data):
        calls.append(data)
        return real_renderer(data)

    monkeypatch.setattr(dispatcher_module, "stringify_snowflake_ids", counting_renderer)

    conns = [_fake_connection(f"c{i}") for i in range(4)]
    dispatcher = GatewayDispatcher(cast(SessionManager, _FakeSessionManager(conns)))
    event = Event(
        event_type=EventType.PRESENCE_UPDATE,
        data={"user_id": 999, "status": "online", "custom_status": None},
    )

    sent = await dispatcher.dispatch_event(event, [999])

    assert sent == 4
    assert len(calls) == 1  # rendered once for all four recipients
    assert dispatcher.get_render_cache_stats()["misses"] == 1
    for conn in conns:
        send = cast(AsyncMock, conn.websocket.send_json)
        assert send.await_args is not None
        frame = send.await_args.args[0]
        assert frame["d"]["user_id"] == "999"
        assert frame["d"]["status"] == "online"


@pytest.mark.asyncio
async def test_dispatcher_reuses_rendering_for_identical_presence_updates(monkeypatch):
    import src.api.websocket.dispatcher as dispatcher_module

    real_renderer = dispatcher_module.stringify_snowflake_ids
    calls = []

    def counting_renderer(data):
        calls.append(data)
        return real_renderer(data)

    monkeypatch.setattr(dispatcher_module, "stringify_snowflake_ids", counting_renderer)

    conns = [_fake_connection(f"c{i}") for i in range(3)]
    dispatcher = GatewayDispatcher(cast(SessionManager, _FakeSessionManager(conns)))
    payload = {"user_id": 5, "status": "idle"}

    for _ in range(3):
        await dispatcher.dispatch_event(
            Event(event_type=EventType.PRESENCE_UPDATE, data=payload), [5]
        )

    assert len(calls) == 1
    assert dispatcher.get_render_cache_stats()["hits"] == 8  # 9 frames, 1 render
