"""
Regression tests for multi-tab ARTIFACT_OP relay (finding M32).

A user may hold several gateway connections (one per tab). The relay must
skip only the ORIGINATING connection of an op -- not the actor's whole user
-- so the actor's other tabs (distinct per-tab ``client_id`` subscribers)
keep receiving live deltas. Excluding by ``user_id`` starved those tabs and
let their optimistic state diverge until a reload.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.artifacts import (
    get_artifact_subscription_registry,
    relay_artifact_op,
)
from src.api.websocket.opcodes import GatewayOpcode

ARTIFACT_ID = 777_777


class _FakeConnection:
    """Connection-shaped double with a per-connection ``sent`` log."""

    def __init__(self, connection_id, user_id):
        self.connection_id = connection_id
        self.user_id = user_id
        self.is_selftest = True
        self.sent = []

    def check_rate_limit(self, per_minute):
        return True


class _FakeSessionManager:
    def __init__(self, connections):
        self._connections = list(connections)

    def get_connections_for_users(self, user_ids):
        wanted = set(user_ids)
        return [c for c in self._connections if c.user_id in wanted]


class _FakeDispatcher:
    def __init__(self, connections):
        self._session_manager = _FakeSessionManager(connections)
        self._rate_limit_per_minute = 1000

    async def _send_to_connection(self, conn, payload):
        conn.sent.append(payload)
        return True


@pytest.fixture
def relay_env(monkeypatch):
    """Wire relay_artifact_op's db/access checks to fakes.

    The artifact is author-scoped (no server/conversation), and the access
    check is stubbed open so the test focuses purely on recipient selection.
    """
    registry = get_artifact_subscription_registry()
    artifact = SimpleNamespace(
        artifact_type="upload",
        server_id=None,
        conversation_id=None,
        author_id=42,
    )

    monkeypatch.setattr("src.api.get_db", lambda: object())
    monkeypatch.setattr(
        "src.core.artifacts.repository.get_artifact", lambda db, aid: artifact
    )
    monkeypatch.setattr(
        "src.core.artifacts.capabilities.capability_allows_artifact",
        lambda *a, **k: True,
    )
    from src.api.websocket.handlers.artifacts import ArtifactHandler

    monkeypatch.setattr(
        ArtifactHandler,
        "_has_artifact_access",
        lambda self, conn, artifact, permission="artifact.view": True,
    )
    try:
        yield
    finally:
        registry.unsubscribe_all(42)
        registry.unsubscribe_all(7)


def _register(registry, conns):
    for conn in conns:
        registry.subscribe(conn.user_id, ARTIFACT_ID, conn.connection_id)


def test_relay_skips_only_originating_connection_of_actor(relay_env):
    """M32: an actor's OTHER tab (same user_id) must still receive the relay."""
    registry = get_artifact_subscription_registry()
    conn_a = _FakeConnection("conn-a", user_id=42)
    conn_b = _FakeConnection("conn-b", user_id=42)  # actor's second tab
    conn_c = _FakeConnection("conn-c", user_id=7)
    _register(registry, [conn_a, conn_b, conn_c])

    dispatcher = _FakeDispatcher([conn_a, conn_b, conn_c])
    op = {"op_type": "edit", "client_id": "tab-a", "content": "hi"}

    sent = asyncio.run(
        relay_artifact_op(
            dispatcher,
            ARTIFACT_ID,
            op,
            actor_id=42,
            exclude_connection_id="conn-a",
            seq=9,
        )
    )

    assert sent == 2
    # Originating connection: no relay (it gets the ack echo separately).
    assert conn_a.sent == []
    # Actor's second tab receives the op with seq + sender client_id, so its
    # own echo-suppression (client_id != tab-b) lets it apply as a remote op.
    assert len(conn_b.sent) == 1
    payload_b = conn_b.sent[0]
    assert payload_b["op"] == int(GatewayOpcode.ARTIFACT_OP)
    assert payload_b["d"]["seq"] == 9
    assert payload_b["d"]["op"]["client_id"] == "tab-a"
    # Unrelated user's tab receives the relay too.
    assert len(conn_c.sent) == 1
    assert conn_c.sent[0]["d"]["op"] == op


def test_relay_checks_access_once_per_user(relay_env):
    """L35: access is a property of the USER, so the relay must not run a
    permission check per connection (a user with N tabs = N module calls per
    op). With three connections across two users the check runs twice."""
    from src.api.websocket.handlers.artifacts import ArtifactHandler

    calls = {"n": 0}
    original = ArtifactHandler._has_artifact_access

    def counting_access(self, conn, artifact, permission="artifact.view"):
        calls["n"] += 1
        return original(self, conn, artifact, permission)

    ArtifactHandler._has_artifact_access = counting_access
    try:
        registry = get_artifact_subscription_registry()
        conn_a = _FakeConnection("conn-a", user_id=42)
        conn_b = _FakeConnection("conn-b", user_id=42)  # same user, second tab
        conn_c = _FakeConnection("conn-c", user_id=7)
        _register(registry, [conn_a, conn_b, conn_c])

        dispatcher = _FakeDispatcher([conn_a, conn_b, conn_c])
        op = {"op_type": "edit", "client_id": "tab-a"}

        sent = asyncio.run(
            relay_artifact_op(
                dispatcher,
                ARTIFACT_ID,
                op,
                actor_id=42,
                exclude_connection_id="conn-a",
                seq=9,
            )
        )

        assert sent == 2  # conn-b (same user) + conn-c
        assert len(conn_b.sent) == 1
        assert len(conn_c.sent) == 1
        # One access check per distinct user (42, 7), not per connection.
        assert calls["n"] == 2
    finally:
        ArtifactHandler._has_artifact_access = original


def test_relay_exclude_user_id_still_excludes_whole_user(relay_env):
    """Backward-compat: exclude_user_id drops every connection of that user."""
    registry = get_artifact_subscription_registry()
    conn_a = _FakeConnection("conn-a", user_id=42)
    conn_b = _FakeConnection("conn-b", user_id=42)
    conn_c = _FakeConnection("conn-c", user_id=7)
    _register(registry, [conn_a, conn_b, conn_c])

    dispatcher = _FakeDispatcher([conn_a, conn_b, conn_c])
    op = {"op_type": "edit", "client_id": "tab-a"}

    sent = asyncio.run(
        relay_artifact_op(
            dispatcher,
            ARTIFACT_ID,
            op,
            actor_id=42,
            exclude_user_id=42,
            seq=3,
        )
    )

    assert sent == 1
    assert conn_a.sent == []
    assert conn_b.sent == []
    assert len(conn_c.sent) == 1


def test_handler_passes_exclude_connection_id(monkeypatch):
    """The ARTIFACT_OP handler excludes by connection, never by whole user."""
    import src.api.websocket.handlers.artifacts as ha

    artifact = SimpleNamespace(
        artifact_type=SimpleNamespace(),
        server_id=None,
        conversation_id=None,
        author_id=42,
    )
    monkeypatch.setattr(ha.api, "get_db", lambda: object())
    monkeypatch.setattr(ha, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(ha, "capability_allows_artifact", lambda *a, **k: True)

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
    conn.connection_id = "conn-42"

    op = {"op_type": "edit", "client_id": "tab-abc", "content": "hello"}
    result = asyncio.run(
        ha.ArtifactHandler().handle_artifact_op(
            conn, {"artifact_id": 77, "op": op}, MagicMock()
        )
    )

    assert result == (None, None, None)
    kwargs = relay_mock.call_args.kwargs
    assert kwargs["exclude_connection_id"] == "conn-42"
    assert "exclude_user_id" not in kwargs
