"""RATCHET_UPDATE fan-out must render its identical ``d`` payload once.

``GatewayDispatcher.broadcast_ratchet_update`` sends the same frame to every
participant of a conversation.  Sequence numbers (``s``) are per-connection and
live outside ``d``, so ``d`` is byte-for-byte identical for the whole fan-out.
Rendering it inside the per-connection loop makes the recursive snowflake walk
(and a fresh dict build) run once per recipient even though the result is
shared.  These tests pin the render-once invariant without changing who
receives the frame, the intent/audience resolution, or the fail-closed checks.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.api.websocket.dispatcher as disp
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.opcodes import GatewayOpcode


N = int(os.environ.get("RATCHET_FANOUT_BENCH_N", "250"))


def _connections(n: int):
    conns = []
    for i in range(n):
        conn = MagicMock()
        conn.is_authenticated = True
        conn.connection_id = f"c{i}"
        conn.session_id = None
        conn.user_id = i
        seq = [0]

        def _incr(_seq=seq):
            _seq[0] += 1
            return _seq[0]

        conn.increment_sequence.side_effect = _incr
        conn.send_json = AsyncMock(return_value=True)
        conns.append(conn)
    return conns


UPDATE = {
    "reason": "rotation",
    "new_interval_id": 999,
    "at_message_id": 12345,
    "at": 1700000000000,
    "extra": {"k": [1, 2, 3, 4, 5]},
}


@pytest.mark.asyncio
async def test_ratchet_update_renders_identical_payload_once():
    """The snowflake renderer runs exactly once for a multi-recipient fan-out."""
    sm = MagicMock()
    dispatcher = GatewayDispatcher(session_manager=sm)
    conns = _connections(N)
    sm.get_connections_for_users.return_value = conns

    real = disp.stringify_snowflake_ids
    rendered = []

    def _counting(data):
        rendered.append(data)
        return real(data)

    with (
        patch("src.core.messaging.get_participant_ids", return_value=list(range(N))),
        patch.object(disp, "stringify_snowflake_ids", side_effect=_counting),
    ):
        sent = await dispatcher.broadcast_ratchet_update(
            conversation_id=42, update_data=UPDATE
        )

    assert sent == N
    # This is the regression: before the fix every connection rendered its own
    # copy (len(rendered) == N); the whole fan-out needs only one rendering.
    assert len(rendered) == 1, (
        f"RATCHET_UPDATE rendered {len(rendered)} times for {N} recipients; "
        "identical payload must be rendered once per broadcast"
    )

    # Safety invariants are unchanged: every recipient still gets a frame, the
    # rendered ``d`` is correct, and the shared render is reused verbatim.
    payloads = [c.send_json.call_args[0][0] for c in conns]
    for p in payloads:
        assert p["op"] == int(GatewayOpcode.RATCHET_UPDATE)
        assert p["t"] == "RATCHET_UPDATE"
        assert p["d"]["conversation_id"] == "42"
        assert p["d"]["reason"] == "rotation"
        assert p["d"]["new_interval_id"] == "999"
        assert p["d"]["at_message_id"] == "12345"
    # The rendered d is one shared object across the fan-out (no per-recipient
    # rebuild), while each connection still gets its own sequence increment.
    assert all(p["d"] is payloads[0]["d"] for p in payloads)
    assert [p["s"] for p in payloads] == [1] * N
    for conn in conns:
        conn.increment_sequence.assert_called_once_with()


@pytest.mark.asyncio
async def test_ratchet_update_render_count_benchmark():
    """Observability helper: print renderer call count + wall time for -s runs."""
    sm = MagicMock()
    dispatcher = GatewayDispatcher(session_manager=sm)
    conns = _connections(N)
    sm.get_connections_for_users.return_value = conns

    real = disp.stringify_snowflake_ids
    calls = [0]

    def _counting(data):
        calls[0] += 1
        return real(data)

    t0 = time.perf_counter()
    with (
        patch("src.core.messaging.get_participant_ids", return_value=list(range(N))),
        patch.object(disp, "stringify_snowflake_ids", side_effect=_counting),
    ):
        await dispatcher.broadcast_ratchet_update(
            conversation_id=42, update_data=UPDATE
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(
        f"\n[RATCHET BENCH] recipients={N} renderer_calls={calls[0]} "
        f"elapsed_ms={elapsed_ms:.2f}"
    )
    assert calls[0] == 1
