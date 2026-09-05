"""
Tests for the artifact op acknowledgement frame used by the OT co-editing
flow. The server assigns each persisted op a ``seq`` and echoes the op back
to its actor with that ``seq`` so the client can commit its optimistic edit.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

from src.api.websocket.artifacts import send_artifact_ack, send_artifact_sync


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


def test_send_artifact_ack_honors_configured_gateway_rate_limit():
    """The ack must respect the dispatcher's limit, not a hardcoded 120."""
    conn = _connection()
    conn.is_selftest = False
    conn.rate_limit_per_minute = 60
    conn.check_rate_limit = Mock(return_value=True)

    result = asyncio.run(
        send_artifact_ack(conn, 777, {"op_type": "edit"}, 1, rate_limit_per_minute=30)
    )

    assert result is True
    conn.check_rate_limit.assert_called_once_with(30)


def test_send_artifact_ack_falls_back_to_connection_rate_limit():
    conn = _connection()
    conn.is_selftest = False
    conn.rate_limit_per_minute = 60
    conn.check_rate_limit = Mock(return_value=True)

    result = asyncio.run(send_artifact_ack(conn, 777, {"op_type": "edit"}, 1))

    assert result is True
    conn.check_rate_limit.assert_called_once_with(60)


def test_send_artifact_sync_honors_configured_gateway_rate_limit():
    conn = _connection()
    conn.is_selftest = False
    conn.rate_limit_per_minute = 60
    conn.check_rate_limit = Mock(return_value=True)

    result = asyncio.run(
        send_artifact_sync(
            conn,
            777,
            {"title": "x"},
            ops=[],
            rate_limit_per_minute=25,
        )
    )

    assert result is True
    conn.check_rate_limit.assert_called_once_with(25)


def test_send_artifact_sync_falls_back_to_connection_rate_limit():
    conn = _connection()
    conn.is_selftest = False
    conn.rate_limit_per_minute = 60
    conn.check_rate_limit = Mock(return_value=True)

    result = asyncio.run(send_artifact_sync(conn, 777, {"title": "x"}, ops=[]))

    assert result is True
    conn.check_rate_limit.assert_called_once_with(60)


def test_subscribe_rolls_back_subscription_when_sync_is_rate_limited(monkeypatch):
    """A rejected ARTIFACT_SYNC must not leave a half-registered subscription."""
    import src.api.websocket.handlers.artifacts as ha
    from src.api.websocket.artifacts import get_artifact_subscription_registry
    from types import SimpleNamespace

    artifact = SimpleNamespace(
        artifact_type=SimpleNamespace(),
        server_id=None,
        conversation_id=None,
        author_id=42,
        payload={"title": "doc"},
    )
    monkeypatch.setattr(ha.api, "get_db", lambda: None)
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)

    conn = MagicMock()
    conn.is_authenticated = True
    conn.user_id = 42
    conn.connection_id = "conn-42"

    registry = get_artifact_subscription_registry()
    try:
        registry.unsubscribe_all(42)
        sync_mock = AsyncMock(return_value=False)
        monkeypatch.setattr(ha, "send_artifact_sync", sync_mock)

        result = asyncio.run(
            ha.ArtifactHandler().handle_artifact_subscribe(
                conn, {"artifact_id": 77}, MagicMock()
            )
        )

        assert result == (None, None, None)
        # The user must NOT remain subscribed after the sync was rejected.
        assert registry.get_subscribers(77) == set()
    finally:
        registry.unsubscribe_all(42)


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
