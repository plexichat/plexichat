"""
Gateway tests for the artifact op rev guard.

An ``edit`` op carrying a numeric ``rev`` older than the artifact's committed
revision (``artifacts.rev`` column) is stale: persisting or relaying it would
pollute the ops log with superseded full-content edits. The gateway must drop
it and send the actor an ``ARTIFACT_OP_REJECTED`` frame so it re-fetches and
re-bases. Ops without a numeric rev (strokes, cursors, PlexiScribe deltas) and
the equal-rev echo of the actor's own just-committed write pass through.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import src.api.websocket.handlers.artifacts as ha
from src.api.websocket.artifacts import send_artifact_op_rejected, send_artifact_sync


def _make_artifact(rev=0, **kwargs):
    attrs = dict(
        artifact_type=SimpleNamespace(),
        server_id=None,
        conversation_id=None,
        author_id=42,
        rev=rev,
    )
    attrs.update(kwargs)
    return SimpleNamespace(**attrs)


def _run_op(monkeypatch, artifact, op):
    """Drive ``handle_artifact_op`` with fakes; return (relay, ack, rejected)."""
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)

    persisted = []

    class FakeManager:
        def __init__(self, db, cfg):
            pass

        def append_op(self, artifact_id, op_type, actor_id, data):
            persisted.append((artifact_id, op_type, actor_id, data))
            return 9

    monkeypatch.setattr("src.core.artifacts.manager.ArtifactManager", FakeManager)

    relay_mock = AsyncMock(return_value=1)
    ack_mock = AsyncMock(return_value=True)
    rejected_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(ha, "relay_artifact_op", relay_mock)
    monkeypatch.setattr(ha, "send_artifact_ack", ack_mock)
    monkeypatch.setattr(ha, "send_artifact_op_rejected", rejected_mock)

    conn = MagicMock()
    conn.is_authenticated = True
    conn.user_id = 42

    result = asyncio.run(
        ha.ArtifactHandler().handle_artifact_op(
            conn,
            {"artifact_id": 77, "op": op},
            SimpleNamespace(_rate_limit_per_minute=None),
        )
    )
    return result, relay_mock, ack_mock, rejected_mock, persisted


def _edit_op(rev, content="hello"):
    return {"op_type": "edit", "rev": rev, "content": content, "client_id": "tab-1"}


def test_stale_edit_op_rejected_and_not_persisted_or_relayed(monkeypatch):
    result, relay, ack, rejected, persisted = _run_op(
        monkeypatch, _make_artifact(rev=5), _edit_op(rev=3)
    )

    assert result == (None, None, None)
    assert persisted == [], "stale op must not be written to the ops log"
    relay.assert_not_awaited()
    ack.assert_not_awaited()
    rejected.assert_awaited_once()
    kwargs = rejected.await_args.kwargs
    assert kwargs["current_rev"] == 5
    # ``op`` is passed positionally (third argument)
    assert rejected.await_args.args[2]["rev"] == 3


def test_equal_rev_op_passes_through(monkeypatch):
    """The equal-rev echo of a just-committed (persist-first) write is allowed."""
    result, relay, ack, rejected, persisted = _run_op(
        monkeypatch, _make_artifact(rev=6), _edit_op(rev=6)
    )

    assert result == (None, None, None)
    assert persisted, "equal-rev op is not stale and must persist"
    relay.assert_awaited_once()
    ack.assert_awaited_once()
    rejected.assert_not_awaited()


def test_newer_rev_op_passes_through(monkeypatch):
    result, _, _, rejected, persisted = _run_op(
        monkeypatch, _make_artifact(rev=5), _edit_op(rev=7)
    )
    assert persisted
    rejected.assert_not_awaited()


def test_edit_op_without_rev_passes_through(monkeypatch):
    op = {"op_type": "edit", "content": "no rev", "client_id": "tab-1"}
    result, _, _, rejected, persisted = _run_op(monkeypatch, _make_artifact(rev=9), op)
    assert persisted
    rejected.assert_not_awaited()


def test_non_edit_op_with_rev_passes_through(monkeypatch):
    op = {"op_type": "stroke", "rev": 1, "points": []}
    result, _, _, rejected, persisted = _run_op(monkeypatch, _make_artifact(rev=9), op)
    assert persisted
    rejected.assert_not_awaited()


def test_send_artifact_op_rejected_frame_shape():
    conn = MagicMock()
    conn.is_selftest = True
    conn.send_json = AsyncMock(return_value=True)

    result = asyncio.run(
        send_artifact_op_rejected(
            conn,
            777,
            {"op_type": "edit", "rev": 3, "client_id": "tab-1"},
            current_rev=5,
        )
    )

    assert result is True
    payload = conn.send_json.call_args.args[0]
    assert payload["op"] == 68  # ARTIFACT_OP_REJECTED
    d = payload["d"]
    assert d["artifact_id"] == "777"
    assert d["reason"] == "stale revision"
    assert d["current_rev"] == 5
    assert d["op"]["client_id"] == "tab-1"


def test_send_artifact_sync_carries_rev():
    conn = MagicMock()
    conn.is_selftest = True
    conn.send_json = AsyncMock(return_value=True)

    result = asyncio.run(
        send_artifact_sync(conn, 777, {"content": "x"}, ops=[], rev=12)
    )

    assert result is True
    payload = conn.send_json.call_args.args[0]
    assert payload["d"]["rev"] == 12


def test_subscribe_passes_artifact_rev_into_sync(monkeypatch):
    """The late-join sync must carry the committed rev so the editor restores it."""
    artifact = _make_artifact(rev=12, payload={"content": "x"})
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)
    monkeypatch.setattr(ha, "list_artifact_ops", lambda *a, **k: []) if hasattr(
        ha, "list_artifact_ops"
    ) else None

    from src.api.websocket.artifacts import get_artifact_subscription_registry

    registry = get_artifact_subscription_registry()
    try:
        registry.unsubscribe_all(42)
        sync_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(ha, "send_artifact_sync", sync_mock)

        conn = MagicMock()
        conn.is_authenticated = True
        conn.user_id = 42
        conn.connection_id = "conn-42"

        result = asyncio.run(
            ha.ArtifactHandler().handle_artifact_subscribe(
                conn, {"artifact_id": 77}, MagicMock()
            )
        )
        assert result == (None, None, None)
        assert sync_mock.await_args.kwargs["rev"] == 12
    finally:
        registry.unsubscribe_all(42)
