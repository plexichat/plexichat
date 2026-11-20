"""Regressions for gateway event audience / scope / intent gates.

Guards two real fan-out weaknesses:

* ``broadcast_ratchet_update`` trusted a caller-supplied connection snapshot
  verbatim, so an over-broad list leaked private-conversation ratchet metadata
  (conversation id, message anchor, timing) to non-participants.
* a cross-worker event with a *present but malformed* ``scope`` envelope fell
  back to data-derived inference, letting a crafted partial envelope choose a
  friendlier intent gate instead of being rejected.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.api.websocket.__init__ as ws_init
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.session import SessionManager

pytestmark = pytest.mark.unit


def _conn(user_id: int, connection_id: str):
    conn = MagicMock()
    conn.is_authenticated = True
    conn.connection_id = connection_id
    conn.session_id = "s-" + connection_id
    conn.intents = 0
    conn.user_id = user_id
    conn.send_json = AsyncMock(return_value=True)
    return conn


@pytest.mark.asyncio
async def test_ratchet_caller_connections_are_participant_scoped():
    """A supplied connection list may not widen the conversation audience."""
    session_manager = MagicMock(spec=SessionManager)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    participant = _conn(1, "c1")
    outsider = _conn(2, "c2")

    with patch(
        "src.core.messaging.get_participant_ids", return_value=[1]
    ) as participants:
        sent = await dispatcher.broadcast_ratchet_update(
            conversation_id=42,
            update_data={"reason": "rotation", "new_interval_id": 99},
            connections=[participant, outsider],
        )

    participants.assert_called_once_with(42)
    assert sent == 1
    participant.send_json.assert_called_once()
    outsider.send_json.assert_not_called()
    # The caller's list is an optimisation only; it must not replace the
    # authoritative participant lookup.
    session_manager.get_connections_for_users.assert_not_called()


@pytest.mark.asyncio
async def test_ratchet_fails_closed_when_participants_unavailable():
    """If membership cannot be resolved, no snapshot is delivered."""
    session_manager = MagicMock(spec=SessionManager)
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    participant = _conn(1, "c1")

    with patch(
        "src.core.messaging.get_participant_ids", side_effect=RuntimeError("db down")
    ):
        sent = await dispatcher.broadcast_ratchet_update(
            conversation_id=42,
            update_data={"reason": "rotation"},
            connections=[participant],
        )

    assert sent == 0
    participant.send_json.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_scope", ["guild", {"server": 1}, [7], [1, 2, 3], 7])
async def test_remote_event_malformed_scope_dropped(session_manager, bad_scope):
    """An explicit scope with the wrong shape is rejected, not reinterpreted."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    target = _conn(111, "c1")
    session_manager.add_connection(target)

    sent = await dispatcher.dispatch_remote_event(
        "MESSAGE_CREATE",
        {"channel_id": "999", "content": "crafted"},
        [111],
        scope=bad_scope,
    )

    assert sent == 0


@pytest.mark.asyncio
async def test_cross_worker_malformed_scope_dropped_end_to_end(monkeypatch):
    """The receive path never downgrades a malformed scope to inference."""
    receiver = MagicMock()
    receiver.dispatch_remote_event = AsyncMock(return_value=1)
    monkeypatch.setattr(ws_init, "_dispatcher", receiver)
    monkeypatch.setattr(ws_init, "_get_worker_id", lambda: "local")

    payload = json.dumps(
        {
            "id": "malformed-scope-1",
            "t": "MESSAGE_CREATE",
            "d": {"channel_id": "999", "content": "crafted"},
            "u": [1],
            "sw": "other",
            "scope": {"server": 7},
        }
    )

    await ws_init._handle_cross_worker_message(payload)

    receiver.dispatch_remote_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_worker_absent_scope_still_uses_fallback(monkeypatch):
    """Legacy publishers without a scope field are unaffected."""
    receiver = MagicMock()
    receiver.dispatch_remote_event = AsyncMock(return_value=1)
    monkeypatch.setattr(ws_init, "_dispatcher", receiver)
    monkeypatch.setattr(ws_init, "_get_worker_id", lambda: "local")

    payload = json.dumps(
        {
            "id": "legacy-no-scope-1",
            "t": "MESSAGE_CREATE",
            "d": {"channel_id": "999", "content": "dm"},
            "u": [1],
            "sw": "other",
        }
    )

    await ws_init._handle_cross_worker_message(payload)

    receiver.dispatch_remote_event.assert_awaited_once_with(
        "MESSAGE_CREATE", {"channel_id": "999", "content": "dm"}, [1]
    )
