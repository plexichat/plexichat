"""Regression: every inbound gateway frame must consume an aggregate ceiling.

``INBOUND_RATE_LIMITS`` caps work *per opcode*, and the completeness guard makes
sure every expensive opcode is present there.  That still leaves the aggregate
frame COUNT unbounded:

* soft-rejected opcodes (``SERVER_STATUS`` and friends) are deliberately NOT in
  the per-opcode table, so a client can loop them at line rate; and
* even fully throttled opcodes have no ceiling on their combined rate.

Each frame still pays a full byte-limit check, UTF-8 decode, ``json.loads`` and
a recursive structural walk before it is dropped, so an unbounded count is an
unbounded CPU/log amplifier regardless of the per-frame size cap.  This module
proves there is one opcode-independent, fail-closed sliding-window budget that
every frame -- including unthrottled ones -- must pass.
"""

from types import SimpleNamespace

import pytest

from src.api.websocket import handlers as handlers_pkg
from src.api.websocket.boundaries import (
    GLOBAL_FRAME_BUCKET_KEY,
    INBOUND_FRAME_WINDOW_S,
    MAX_INBOUND_FRAMES_PER_CONNECTION,
)
from src.api.websocket.handlers import INBOUND_RATE_LIMITS, OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager

pytestmark = pytest.mark.websocket


def _connection() -> SimpleNamespace:
    # Minimal transport double: OpcodeHandler only reads these attributes on
    # the inbound admission path (no socket, no DB).
    return SimpleNamespace(
        connection_id="aggregate-flood-conn",
        _inbound_rate_buckets={},
        is_authenticated=True,
        is_selftest=False,
        user_id=42,
    )


def test_aggregate_limit_constant_is_bounded():
    """The ceiling is a positive, finite bound exposed for enforcement."""
    assert isinstance(MAX_INBOUND_FRAMES_PER_CONNECTION, int)
    assert 0 < MAX_INBOUND_FRAMES_PER_CONNECTION < 10_000
    assert INBOUND_FRAME_WINDOW_S > 0
    assert isinstance(GLOBAL_FRAME_BUCKET_KEY, int)


def test_unthrottled_opcode_consumes_the_aggregate_budget():
    """A soft-reject opcode has no per-opcode limit but IS globally capped."""
    op = int(GatewayOpcode.SERVER_STATUS)
    assert op not in INBOUND_RATE_LIMITS  # this is the whole point of the test

    handler = OpcodeHandler(session_manager=SessionManager())
    conn = _connection()

    for _ in range(MAX_INBOUND_FRAMES_PER_CONNECTION):
        assert handler._allow_inbound(conn, op) is True

    # One frame over the aggregate budget must be refused, not admitted.
    assert handler._allow_inbound(conn, op) is False


def test_aggregate_budget_is_shared_across_opcodes():
    """Different opcodes draw from the same aggregate bucket."""
    handler = OpcodeHandler(session_manager=SessionManager())
    conn = _connection()

    half = MAX_INBOUND_FRAMES_PER_CONNECTION // 2
    for _ in range(half):
        assert handler._allow_inbound(conn, int(GatewayOpcode.SERVER_STATUS))
    for _ in range(MAX_INBOUND_FRAMES_PER_CONNECTION - half):
        assert handler._allow_inbound(conn, int(GatewayOpcode.VERSION_CHECK))

    assert handler._allow_inbound(conn, int(GatewayOpcode.SERVER_STATUS)) is False


@pytest.mark.asyncio
async def test_handle_drops_over_budget_frames_before_soft_reject(monkeypatch):
    """End to end: excess frames never reach even the cheap soft-reject path."""
    op = int(GatewayOpcode.SERVER_STATUS)
    handler = OpcodeHandler(session_manager=SessionManager())
    conn = _connection()
    soft_rejected = []
    rate_limited = []

    def _debug(msg, *args):
        if "soft-rejected" in msg:
            soft_rejected.append(msg)
        elif "rate-limited" in msg:
            rate_limited.append(msg)

    monkeypatch.setattr(handlers_pkg.logger, "debug", _debug)

    sent = MAX_INBOUND_FRAMES_PER_CONNECTION + 25
    for _ in range(sent):
        outcome = await handler.handle(conn, op, {})
        assert outcome == (None, None, None)

    assert len(soft_rejected) == MAX_INBOUND_FRAMES_PER_CONNECTION, (
        "over-budget frames reached the soft-reject path; the aggregate "
        f"ceiling admitted {len(soft_rejected)} of {sent}"
    )
    assert len(rate_limited) == 25, "excess frames were not dropped fail-closed"
