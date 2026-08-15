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
    assert d["artifact_id"] == 777
    assert d["actor_id"] == 42
    assert d["seq"] == 9
    assert d["op"] == op


def test_send_artifact_ack_includes_none_actor_when_unknown():
    conn = _connection(user_id=None)
    result = asyncio.run(send_artifact_ack(conn, 777, {"op_type": "edit"}, 2))
    assert result is True
    assert conn.send_json.call_args.args[0]["d"]["actor_id"] is None
