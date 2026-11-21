"""Server broadcasts share one structural payload freeze across recipients.

``dispatch_event``, ``dispatch_raw_to_users`` and the RATCHET_UPDATE/STATUS
broadcasts already hoist the ``freeze_payload`` walk out of their fan-out loops.
``broadcast_to_server`` was the one remaining identical-payload fan-out that
still rebuilt the key once per recipient inside ``PayloadRenderCache.render``.

The observable counter is ``get_render_cache_stats()["key_builds"]``: before the
fix a six-recipient server broadcast reported 6 (one recursive freeze per
recipient); after the fix it reports 0 because the caller supplies the frozen
key once.
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.intents import ALL_INTENTS
from src.api.websocket.session import SessionManager
from src.core.events.models import Event
from src.core.events.types import EventType


class _FakeSessionManager:
    def __init__(self, connections):
        self._connections = connections

    def get_connections_for_users(self, user_ids):
        return list(self._connections)

    def record_event(self, session_id, event):
        pass


def _fake_connection(name):
    conn = Connection(websocket=AsyncMock(), connection_id=name, intents=ALL_INTENTS)
    conn.state = ConnectionState.READY
    conn.user_id = 7
    conn.is_selftest = True
    return conn


@pytest.mark.asyncio
async def test_broadcast_to_server_freezes_payload_once(monkeypatch):
    """One structural freeze per server broadcast, not one per member connection."""
    conns = [_fake_connection(f"c{i}") for i in range(6)]
    dispatcher = GatewayDispatcher(cast(SessionManager, _FakeSessionManager(conns)))
    # Resolve the member audience without a servers module; audience resolution
    # is not what this regression measures.
    monkeypatch.setattr(dispatcher, "_resolve_server_member_ids", lambda sid: {7})

    data = {
        "user_id": 999,
        "status": "online",
        "embeds": [{"title": "t", "fields": [{"name": "a", "value": "b"}]}],
    }
    event = Event(event_type=EventType.GUILD_MEMBER_UPDATE, data=data)

    sent = await dispatcher.broadcast_to_server(7, event)

    assert sent == 6
    # The caller freezes once; render() must not rebuild the structural key for
    # any of the six recipients (the old path built it 6 times here).
    stats = dispatcher.get_render_cache_stats()
    assert stats["key_builds"] == 0
    assert stats["misses"] == 1
    assert stats["hits"] == 5
    for conn in conns:
        frame = cast(AsyncMock, conn.websocket.send_json).await_args.args[0]
        assert frame["d"]["user_id"] == "999"
        assert frame["d"]["status"] == "online"
