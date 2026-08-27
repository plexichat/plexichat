"""
Tests for the artifact op acknowledgement frame used by the OT co-editing
flow. The server assigns each persisted op a ``seq`` and echoes the op back
to its actor with that ``seq`` so the client can commit its optimistic edit.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.api.websocket.artifacts import send_artifact_ack


def _connection(user_id=42):
    conn = MagicMock()
    conn.is_selftest = True
    conn.user_id = user_id
    conn.send_json = AsyncMock(return_value=True)
    return conn


def test_send_artifact_ack_emits_op_with_seq():
    conn = _connection()
    op = {
        "op_type": "text",
        "block_id": "b1",
        "ops": [0, "x"],
        "client_id": "c1",
        "clock": 3,
    }

    result = asyncio.run(send_artifact_ack(conn, 777, op, 9))

    assert result is True
    payload = conn.send_json.call_args.args[0]
    assert payload["op"] == 62  # ARTIFACT_OP
    d = payload["d"]
    assert str(d["artifact_id"]) == "777"
    assert str(d["actor_id"]) == "42"
    assert d["seq"] == 9
    assert d["op"] == op


def test_send_artifact_ack_includes_none_actor_when_unknown():
    conn = _connection(user_id=None)
    result = asyncio.run(send_artifact_ack(conn, 777, {"op_type": "edit"}, 2))
    assert result is True
    assert conn.send_json.call_args.args[0]["d"]["actor_id"] is None


def test_handle_artifact_op_preserves_client_id_in_relay_and_ack(monkeypatch):
    """The gateway must carry the sender's ``client_id`` through BOTH the
    relay and the ack echo so clients can recognize their own ops."""
    import src.api.websocket.handlers.artifacts as ha
    from types import SimpleNamespace

    artifact = SimpleNamespace(
        artifact_type=SimpleNamespace(),
        server_id=None,
        conversation_id=None,
        author_id=42,
    )
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *args, **kwargs: True)

    class FakeManager:
        def __init__(self, db, cfg):
            pass

        def append_op(self, artifact_id, op_type, actor_id, data):
            return 9

    monkeypatch.setattr("src.core.artifacts.manager.ArtifactManager", FakeManager)
    relay_mock = AsyncMock(return_value=1)
    ack_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "relay_artifact_op", relay_mock)
    monkeypatch.setattr(ha, "send_artifact_ack", ack_mock)

    conn = MagicMock()
    conn.is_authenticated = True
    conn.user_id = 42

    op = {"op_type": "edit", "client_id": "tab-abc", "content": "hello"}
    result = asyncio.run(
        ha.ArtifactHandler().handle_artifact_op(
            conn, {"artifact_id": 77, "op": op}, MagicMock()
        )
    )

    assert result == (None, None, None)
    relayed = relay_mock.call_args.kwargs["op"]
    assert relayed["client_id"] == "tab-abc"
    acked = ack_mock.call_args.args[2]
    assert acked["client_id"] == "tab-abc"
