"""Ratchet: PlexiSum / PlexiStash WebSocket frames stringify nested id fields.

The sum/artifact fabric helpers send through ``connection.send_json`` (or the
dispatcher's private ``_send_to_connection``) directly, bypassing
``GatewayDispatcher._build_dispatch_payload`` and its
``stringify_snowflake_ids`` backstop. The top-level ids were already
stringified, but the *nested* ``op`` / ``ops`` / ``snapshot`` bodies were passed
through verbatim — a ``sheet_id`` / ``block_id`` / ``actor_id`` in there would
cross the wire as a JSON number and be rounded by the client past 2**53.

These tests drive every direct-send helper with ids above 2**53 and assert the
exact decimal string lands in the emitted frame.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.api.websocket import artifacts as art_ws
from src.api.websocket import sum as sum_ws

# First integer a JS ``Number`` cannot represent.
BIG = 9007199254740993


class _FakeConnection:
    def __init__(self, user_id: int = 42) -> None:
        self.connection_id = "conn-1"
        self.user_id = user_id
        self.is_selftest = True
        self.rate_limit_per_minute = 120
        self.sent: list = []

    async def send_json(self, payload):
        self.sent.append(payload)
        return True

    def check_rate_limit(self, limit):
        return True


class _FakeDispatcher:
    def __init__(self, connections) -> None:
        self._rate_limit_per_minute = 120
        self._session_manager = SimpleNamespace(
            get_connections_for_users=lambda user_ids: list(connections)
        )
        self.sent: list = []

    async def _send_to_connection(self, connection, payload):
        self.sent.append(payload)
        return True


@pytest.mark.asyncio
async def test_send_sum_sync_stringifies_nested_op_ids() -> None:
    conn = _FakeConnection()
    ok = await sum_ws.send_sum_sync(
        conn,
        BIG,
        5,
        ops=[{"actor_id": BIG + 1, "op": {"sheet_id": BIG + 2}}],
        rate_limit_per_minute=120,
    )
    assert ok is True

    d = conn.sent[-1]["d"]
    assert d["workbook_id"] == str(BIG)
    assert d["ops"][0]["actor_id"] == str(BIG + 1)
    assert d["ops"][0]["op"]["sheet_id"] == str(BIG + 2)
    # Non-id numbers stay numeric.
    assert d["revision"] == 5


@pytest.mark.asyncio
async def test_relay_sum_op_stringifies_nested_op_ids(monkeypatch) -> None:
    monkeypatch.setattr(sum_ws, "_capability_allows_sum", lambda: True)
    manager = Mock()
    manager.get_workbook.return_value = {"id": BIG}
    monkeypatch.setattr(sum_ws, "_get_sum_manager", lambda: manager)

    conn = _FakeConnection(user_id=7)
    sum_ws.get_sum_subscription_registry().subscribe(7, BIG, conn.connection_id)
    dispatcher = _FakeDispatcher([conn])

    sent = await sum_ws.relay_sum_op(
        dispatcher, BIG, {"sheet_id": BIG + 1, "client_id": BIG + 2}, BIG + 3
    )
    assert sent == 1

    d = dispatcher.sent[-1]["d"]
    assert d["workbook_id"] == str(BIG)
    assert d["actor_id"] == str(BIG + 3)
    assert d["op"]["sheet_id"] == str(BIG + 1)
    assert d["op"]["client_id"] == str(BIG + 2)


@pytest.mark.asyncio
async def test_send_artifact_sync_stringifies_snapshot_and_ops_ids() -> None:
    conn = _FakeConnection()
    snapshot = {
        "artifact_id": BIG + 4,
        "author_id": BIG + 5,
        # A whiteboard layer id is already a string; it must be untouched.
        "layers": [{"id": "layer-1", "name": "Layer 1"}],
    }
    ok = await art_ws.send_artifact_sync(
        conn,
        BIG,
        snapshot,
        ops=[{"actor_id": BIG + 6, "op": {"block_id": BIG + 7}}],
        rev=3,
    )
    assert ok is True

    d = conn.sent[-1]["d"]
    assert d["artifact_id"] == str(BIG)
    assert d["snapshot"]["artifact_id"] == str(BIG + 4)
    assert d["snapshot"]["author_id"] == str(BIG + 5)
    assert d["snapshot"]["layers"][0]["id"] == "layer-1"
    assert d["ops"][0]["actor_id"] == str(BIG + 6)
    assert d["ops"][0]["op"]["block_id"] == str(BIG + 7)
    assert d["rev"] == 3


@pytest.mark.asyncio
async def test_send_artifact_ack_stringifies_nested_op_ids() -> None:
    conn = _FakeConnection(user_id=BIG + 8)
    ok = await art_ws.send_artifact_ack(
        conn, BIG, {"block_id": BIG + 9, "client_id": "client-1"}, seq=4
    )
    assert ok is True

    d = conn.sent[-1]["d"]
    assert d["artifact_id"] == str(BIG)
    assert d["actor_id"] == str(BIG + 8)
    assert d["op"]["block_id"] == str(BIG + 9)
    assert d["op"]["client_id"] == "client-1"


@pytest.mark.asyncio
async def test_send_artifact_op_rejected_stringifies_nested_op_ids() -> None:
    conn = _FakeConnection()
    ok = await art_ws.send_artifact_op_rejected(
        conn, BIG, {"block_id": BIG + 9}, current_rev=2
    )
    assert ok is True

    d = conn.sent[-1]["d"]
    assert d["artifact_id"] == str(BIG)
    assert d["op"]["block_id"] == str(BIG + 9)
    assert d["current_rev"] == 2


@pytest.mark.asyncio
async def test_relay_artifact_op_stringifies_nested_op_ids(monkeypatch) -> None:
    import src.api as api
    from src.core.artifacts import capabilities as caps
    from src.core.artifacts import repository as repo
    from src.api.websocket.handlers import artifacts as handlers_artifacts

    artifact = SimpleNamespace(
        artifact_type="whiteboard",
        server_id=None,
        conversation_id=None,
        author_id=7,
    )
    monkeypatch.setattr(api, "get_db", lambda: object())
    monkeypatch.setattr(repo, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(caps, "capability_allows_artifact", lambda *a, **k: True)
    # ``_has_artifact_access`` binds the symbol at import time.
    monkeypatch.setattr(
        handlers_artifacts, "capability_allows_artifact", lambda *a, **k: True
    )

    conn = _FakeConnection(user_id=7)
    art_ws.get_artifact_subscription_registry().subscribe(7, BIG, conn.connection_id)
    dispatcher = _FakeDispatcher([conn])

    sent = await art_ws.relay_artifact_op(
        dispatcher,
        BIG,
        {"block_id": BIG + 1, "client_id": "client-1"},
        BIG + 2,
        forward_to_federation=False,
    )
    assert sent == 1

    d = dispatcher.sent[-1]["d"]
    assert d["artifact_id"] == str(BIG)
    assert d["actor_id"] == str(BIG + 2)
    assert d["op"]["block_id"] == str(BIG + 1)
    assert d["op"]["client_id"] == "client-1"
