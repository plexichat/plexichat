"""
PlexiSum real-time fabric tests.

Covers the Sum subscription registry and the gateway fabric helpers/handlers
(opcodes 64-67): subscribe/unsubscribe semantics (multi-tab safe), relay
authorization revalidation, late-joiner SUM_SYNC bootstrap, op application
with idempotent replay, and conflict recovery via SUM_SYNC.
"""

import pytest

from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.sum import (
    SumSubscriptionRegistry,
    get_sum_subscription_registry,
    relay_sum_op,
)
from src.api.websocket.handlers.sum import SumHandler
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum.manager import SumManager


# === Fakes =================================================================


class FakeConnection:
    """Minimal Connection stand-in used by the fabric tests."""

    def __init__(self, user_id, connection_id="conn", rate_ok=True):
        self.user_id = user_id
        self.connection_id = connection_id
        self.rate_ok = rate_ok
        self.is_selftest = False
        self.is_authenticated = True
        self.sent = []

    def check_rate_limit(self, _limit):
        return self.rate_ok

    async def send_json(self, payload):
        self.sent.append(payload)
        return True


class FakeSessionManager:
    def __init__(self, connections):
        self._connections = connections

    def get_connections_for_users(self, user_ids):
        wanted = set(int(uid) for uid in user_ids)
        return [c for c in self._connections if c.user_id in wanted]


class FakeDispatcher:
    def __init__(self, connections, rate_limit_per_minute=120):
        self._session_manager = FakeSessionManager(connections)
        self._rate_limit_per_minute = rate_limit_per_minute

    async def _send_to_connection(self, connection, payload):
        connection.sent.append(payload)
        return True


@pytest.fixture
def sum_manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


@pytest.fixture
def workbook(sum_manager, two_users):
    owner, _other = two_users
    return sum_manager.create_workbook(owner.id, "Realtime workbook")


@pytest.fixture
def patch_fabric(monkeypatch, sum_manager):
    """Point the fabric's manager/capability resolution at the test manager."""
    monkeypatch.setattr("src.api.websocket.sum._get_sum_manager", lambda: sum_manager)
    monkeypatch.setattr(
        "src.api.websocket.handlers.sum._get_sum_manager", lambda: sum_manager
    )
    monkeypatch.setattr("src.api.websocket.sum._capability_allows_sum", lambda: True)


# === Registry ==============================================================


def test_sum_registry_subscribe_unsubscribe_multitab():
    registry = SumSubscriptionRegistry()
    registry.subscribe(10, 100, "conn-a")
    registry.subscribe(10, 100, "conn-b")
    registry.subscribe(11, 100, "conn-c")

    assert registry.get_subscribers(100) == {10, 11}

    # One tab leaving must not drop the user's subscription while another tab
    # is still subscribed.
    registry.unsubscribe_connection("conn-a", 10)
    assert registry.get_subscribers(100) == {10, 11}

    registry.unsubscribe_connection("conn-b", 10)
    assert registry.get_subscribers(100) == {11}

    registry.unsubscribe(11, 100, "conn-c")
    assert registry.get_subscribers(100) == set()


def test_sum_registry_unsubscribe_preserves_sibling_tabs():
    """An explicit SUM_UNSUBSCRIBE from one tab must not drop the user's other
    tab from the global subscriber set (regression: the old implementation
    discarded the user_id unconditionally)."""
    registry = SumSubscriptionRegistry()
    registry.subscribe(10, 100, "conn-a")
    registry.subscribe(10, 100, "conn-b")

    assert registry.get_subscribers(100) == {10}

    # conn-a explicitly unsubscribes; conn-b must keep receiving relays.
    registry.unsubscribe(10, 100, "conn-a")
    assert registry.get_subscribers(100) == {10}

    # Only when the last tab unsubscribes is the user removed.
    registry.unsubscribe(10, 100, "conn-b")
    assert registry.get_subscribers(100) == set()


def test_sum_registry_unsubscribe_without_connection_removes_user():
    """Without a connection id, unsubscribe is a full user removal."""
    registry = SumSubscriptionRegistry()
    registry.subscribe(10, 100, "conn-a")
    registry.subscribe(10, 100, "conn-b")
    registry.unsubscribe(10, 100)
    assert registry.get_subscribers(100) == set()


def test_sum_registry_unsubscribe_all():
    registry = SumSubscriptionRegistry()
    registry.subscribe(10, 100, "conn-a")
    registry.subscribe(10, 200, "conn-b")
    registry.unsubscribe_all(10)
    assert registry.get_subscribers(100) == set()
    assert registry.get_subscribers(200) == set()


# === Relay =================================================================


@pytest.mark.asyncio
async def test_sum_relay_delivers_to_authorized_subscribers_excludes_actor(
    sum_manager, workbook, two_users, patch_fabric, monkeypatch
):
    owner, other = two_users
    workbook_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])
    sum_manager.grant_workbook(owner.id, workbook_id, other.id, ["view"])

    owner_conn = FakeConnection(owner.id, "conn-owner")
    other_conn = FakeConnection(other.id, "conn-other")
    intruder_conn = FakeConnection(999_999, "conn-intruder")
    dispatcher = FakeDispatcher([owner_conn, other_conn, intruder_conn])

    registry = get_sum_subscription_registry()
    registry.subscribe(owner.id, workbook_id, "conn-owner")
    registry.subscribe(other.id, workbook_id, "conn-other")
    registry.subscribe(999_999, workbook_id, "conn-intruder")
    try:
        op = {
            "operation_id": "op-relay",
            "operation_type": "set_cell",
            "sheet_id": sheet_id,
            "base_revision": 0,
            "result_revision": 1,
            "cells": [{"row": 0, "column": 0, "value_type": "number", "value": 7}],
        }
        sent = await relay_sum_op(
            dispatcher, workbook_id, op, owner.id, exclude_user_id=owner.id
        )
        # Actor excluded; grantee receives; the unauthorized user is skipped
        # even though a stale subscription routes to them.
        assert sent == 1
        assert len(other_conn.sent) == 1
        frame = other_conn.sent[0]
        assert frame["op"] == int(GatewayOpcode.SUM_OP)
        assert frame["d"]["workbook_id"] == workbook_id
        assert frame["d"]["actor_id"] == owner.id
        assert frame["d"]["op"]["operation_id"] == "op-relay"
        assert owner_conn.sent == []
        assert intruder_conn.sent == []
    finally:
        registry.unsubscribe_all(owner.id)
        registry.unsubscribe_all(other.id)
        registry.unsubscribe_all(999_999)


@pytest.mark.asyncio
async def test_sum_subscribe_resync_after_rate_limit(
    sum_manager, workbook, two_users, patch_fabric
):
    """A rate-limited SUM_SYNC must not leave a dangling subscription."""
    owner, _other = two_users
    workbook_id = int(workbook["id"])
    conn = FakeConnection(owner.id, "conn-rl-sync", rate_ok=False)
    dispatcher = FakeDispatcher([conn], rate_limit_per_minute=60)
    handler = SumHandler()

    result = await handler.handle_sum_subscribe(
        conn, {"workbook_id": workbook_id, "base_revision": 0}, dispatcher
    )
    assert result == (None, None, None)
    # The sync frame was rejected by the rate limit, so no frame and no
    # lingering subscription for the workbook.
    assert conn.sent == []
    registry = get_sum_subscription_registry()
    assert workbook_id not in registry.get_subscribers(workbook_id)


@pytest.mark.asyncio
async def test_sum_relay_skips_rate_limited_connections(
    sum_manager, workbook, two_users, patch_fabric
):
    owner, other = two_users
    workbook_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])
    other_conn = FakeConnection(other.id, "conn-other", rate_ok=False)
    dispatcher = FakeDispatcher([other_conn])

    registry = get_sum_subscription_registry()
    registry.subscribe(other.id, workbook_id, "conn-other")
    try:
        op = {
            "operation_id": "op-rl",
            "operation_type": "set_cell",
            "sheet_id": sheet_id,
            "base_revision": 0,
            "result_revision": 1,
            "cells": [],
        }
        sent = await relay_sum_op(dispatcher, workbook_id, op, owner.id)
        assert sent == 0
        assert other_conn.sent == []
    finally:
        registry.unsubscribe_all(other.id)


# === Handler: subscribe ===================================================


@pytest.mark.asyncio
async def test_sum_handler_subscribe_sends_sync_bootstrap(
    sum_manager, workbook, two_users, patch_fabric
):
    owner, _other = two_users
    workbook_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])
    sum_manager.apply_operation(
        owner.id,
        workbook_id,
        sheet_id,
        "op-bootstrap",
        "set_cell",
        0,
        [{"row": 1, "column": 1, "value_type": "number", "value": 5}],
    )

    conn = FakeConnection(owner.id, "conn-sub")
    dispatcher = FakeDispatcher([conn])
    handler = SumHandler()
    try:
        result = await handler.handle_sum_subscribe(
            conn, {"workbook_id": workbook_id, "base_revision": 0}, dispatcher
        )
        assert result == (None, None, None)
        assert len(conn.sent) == 1
        sync = conn.sent[0]
        assert sync["op"] == int(GatewayOpcode.SUM_SYNC)
        assert sync["d"]["workbook_id"] == workbook_id
        assert sync["d"]["revision"] == 1
        assert len(sync["d"]["ops"]) == 1
        assert sync["d"]["ops"][0]["operation_id"] == "op-bootstrap"
    finally:
        get_sum_subscription_registry().unsubscribe_all(owner.id)


@pytest.mark.asyncio
async def test_sum_handler_subscribe_denied_for_unauthorized(
    sum_manager, workbook, two_users, patch_fabric
):
    _owner, other = two_users
    workbook_id = int(workbook["id"])
    conn = FakeConnection(other.id, "conn-denied")
    dispatcher = FakeDispatcher([conn])
    handler = SumHandler()
    result = await handler.handle_sum_subscribe(
        conn, {"workbook_id": workbook_id}, dispatcher
    )
    # Resource-level denial must NOT close the gateway connection (that would
    # trap authenticated clients in a reconnect loop); the subscribe is just
    # silently refused with no sync payload.
    assert result == (None, None, None)
    assert conn.sent == []
    assert other.id not in get_sum_subscription_registry().get_subscribers(workbook_id)


# === Handler: op ===========================================================


@pytest.mark.asyncio
async def test_sum_handler_op_applies_and_relays(
    sum_manager, workbook, two_users, patch_fabric
):
    owner, other = two_users
    workbook_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])
    # The viewer needs an explicit grant; the relay revalidates recipients
    # against the Sum domain before fan-out, so an ungranted subscriber is
    # skipped even if a stale subscription routes to them.
    sum_manager.grant_workbook(owner.id, workbook_id, other.id, ["view"])
    owner_conn = FakeConnection(owner.id, "conn-actor")
    other_conn = FakeConnection(other.id, "conn-viewer")
    dispatcher = FakeDispatcher([owner_conn, other_conn])

    registry = get_sum_subscription_registry()
    registry.subscribe(other.id, workbook_id, "conn-viewer")
    try:
        handler = SumHandler()
        result = await handler.handle_sum_op(
            owner_conn,
            {
                "workbook_id": workbook_id,
                "op": {
                    "operation_id": "op-apply",
                    "operation_type": "set_cell",
                    "sheet_id": sheet_id,
                    "base_revision": 0,
                    "cells": [
                        {"row": 3, "column": 3, "value_type": "text", "value": "hi"}
                    ],
                },
            },
            dispatcher,
        )
        assert result == (None, None, None)

        # The op is persisted and replayable.
        replay = sum_manager.replay_operations(owner.id, workbook_id)
        assert len(replay) == 1
        assert replay[0]["operation_id"] == "op-apply"

        # The other subscriber received the relayed delta.
        assert len(other_conn.sent) == 1
        frame = other_conn.sent[0]
        assert frame["op"] == int(GatewayOpcode.SUM_OP)
        assert frame["d"]["op"]["result_revision"] == 1
    finally:
        registry.unsubscribe_all(other.id)


@pytest.mark.asyncio
async def test_sum_handler_op_conflict_returns_sync(
    sum_manager, workbook, two_users, patch_fabric
):
    owner, _other = two_users
    workbook_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])
    conn = FakeConnection(owner.id, "conn-conflict")
    dispatcher = FakeDispatcher([conn])
    handler = SumHandler()

    result = await handler.handle_sum_op(
        conn,
        {
            "workbook_id": workbook_id,
            "op": {
                "operation_id": "op-stale",
                "operation_type": "set_cell",
                "sheet_id": sheet_id,
                "base_revision": 99,
                "cells": [{"row": 0, "column": 0, "value_type": "text", "value": "x"}],
            },
        },
        dispatcher,
    )
    response_op, response_data, close_code = result
    assert close_code is None
    assert response_op == int(GatewayOpcode.SUM_SYNC)
    assert response_data["workbook_id"] == workbook_id
    assert response_data["revision"] == 0
    assert response_data["ops"] == []
