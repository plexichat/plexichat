"""Regression: ARTIFACT_UNSUBSCRIBE must carry a per-connection inbound ceiling.

ARTIFACT_UNSUBSCRIBE is a mutating fabric opcode that the inbound throttle
sweep missed. On every frame it calls
``ArtifactHandler.reauthorize_subscriptions()``, which re-checks EVERY artifact
subscription held by the connection against the DB: ``get_artifact()`` plus a
scope permission / conversation-membership lookup per subscription (up to
128 subscriptions per connection). A single identified socket can loop the
opcode for unbounded synchronous DB / threadpool work on the gateway loop.

``INBOUND_RATE_LIMITS`` is the only per-connection inbound frame-rate guard on
that path. These tests drive an over-budget burst through
``OpcodeHandler.handle`` and prove the excess frames are dropped before the
heavy handler runs. A non-vacuity check removes the ceiling and shows the same
burst DOES reach the handler, so the test cannot pass for the wrong reason.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.handlers import INBOUND_RATE_LIMITS, OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager

pytestmark = pytest.mark.websocket


def _authenticated_connection() -> SimpleNamespace:
    return SimpleNamespace(
        connection_id="artifact-unsub-flood-conn",
        _inbound_rate_buckets={},
        is_authenticated=True,
        is_selftest=False,
        user_id=42,
    )


def _handler_with_spy(monkeypatch):
    handler = OpcodeHandler(session_manager=SessionManager())
    unsubscribe_spy = AsyncMock(return_value=(None, None, None))
    monkeypatch.setattr(
        handler._artifact_handler, "handle_artifact_unsubscribe", unsubscribe_spy
    )
    return handler, unsubscribe_spy


def test_artifact_unsubscribe_is_throttled_in_rate_table():
    """ARTIFACT_UNSUBSCRIBE must be present in the real throttle table."""
    op = int(GatewayOpcode.ARTIFACT_UNSUBSCRIBE)
    assert op in INBOUND_RATE_LIMITS, (
        "ARTIFACT_UNSUBSCRIBE mutates state and re-authorizes every artifact "
        "subscription via DB reads on every frame; it must be throttled"
    )
    budget, window = INBOUND_RATE_LIMITS[op]
    assert budget > 0 and window > 0


@pytest.mark.asyncio
async def test_artifact_unsubscribe_burst_is_capped_before_heavy_handler(monkeypatch):
    """Over-budget ARTIFACT_UNSUBSCRIBE frames never reach the dispatch path."""
    op = int(GatewayOpcode.ARTIFACT_UNSUBSCRIBE)
    budget, _window = INBOUND_RATE_LIMITS[op]
    handler, unsubscribe_spy = _handler_with_spy(monkeypatch)
    conn = _authenticated_connection()

    sent = budget + 25
    for _ in range(sent):
        outcome = await handler.handle(conn, op, {"artifact_id": "123"})
        # A throttled frame is dropped silently: no close, no error response.
        assert outcome == (None, None, None)

    assert unsubscribe_spy.await_count == budget, (
        f"{sent} ARTIFACT_UNSUBSCRIBE frames reached the heavy handler "
        f"{unsubscribe_spy.await_count} times; expected at most {budget}"
    )
    # The budget is not zero: legitimate unsubscribes still flow.
    assert unsubscribe_spy.await_count > 0


@pytest.mark.asyncio
async def test_burst_cap_is_due_to_throttle_not_an_early_return(monkeypatch):
    """Non-vacuity: with the ceiling removed the same burst all lands."""
    op = int(GatewayOpcode.ARTIFACT_UNSUBSCRIBE)
    budget, _window = INBOUND_RATE_LIMITS[op]
    handler, unsubscribe_spy = _handler_with_spy(monkeypatch)
    conn = _authenticated_connection()

    # The frames are genuinely routable: without the throttle entry the
    # identical burst reaches the heavy handler every single time. This proves
    # the cap above is caused by the inbound ceiling, not by some unrelated
    # early rejection (auth gate, payload validation, unknown opcode, ...).
    monkeypatch.delitem(INBOUND_RATE_LIMITS, op)
    sent = budget + 25
    for _ in range(sent):
        outcome = await handler.handle(conn, op, {"artifact_id": "123"})
        assert outcome == (None, None, None)

    assert unsubscribe_spy.await_count == sent, (
        f"without the throttle only {unsubscribe_spy.await_count}/{sent} frames "
        "reached the handler; the burst is being capped by something else"
    )
