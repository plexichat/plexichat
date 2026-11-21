"""SERVER_STATUS fan-out must render its identical payload once.

``GatewayDispatcher.broadcast_server_status`` sends the same frame to every
connected client.  Unlike DISPATCH frames there is no per-connection sequence
number or event type: the entire frame is byte-for-byte identical for the whole
broadcast.  Rendering the snowflakes (and building a fresh dict) inside the
per-connection loop made the recursive walk run once per recipient even though
every copy was identical.  These tests pin the render-once invariant without
changing who receives the frame or the stringify backstop contract.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.api.websocket.dispatcher as disp
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.opcodes import GatewayOpcode


N = int(os.environ.get("SERVER_STATUS_FANOUT_BENCH_N", "250"))

STATUS = {
    "state": "shutting_down",
    "message": "restarting",
    "server_id": 1234567890123456789,
    "channel_id": 9876543210987654321,
    "closing_in_seconds": 2.0,
    "nested": {"guild_id": 555555555555555555, "roles": [1, 2, 3, 4, 5]},
}


def _connections(n: int):
    conns = []
    for i in range(n):
        conn = MagicMock()
        conn.is_authenticated = True
        conn.connection_id = f"c{i}"
        conn.session_id = None
        conn.user_id = i
        conn.send_json = AsyncMock(return_value=True)
        conns.append(conn)
    return conns


@pytest.mark.asyncio
async def test_server_status_renders_identical_payload_once():
    """The snowflake renderer runs exactly once for a multi-recipient broadcast."""
    sm = MagicMock()
    dispatcher = GatewayDispatcher(session_manager=sm)
    conns = _connections(N)

    real = disp.stringify_snowflake_ids
    rendered = []

    def _counting(data):
        rendered.append(data)
        return real(data)

    with patch.object(disp, "stringify_snowflake_ids", side_effect=_counting):
        sent = await dispatcher.broadcast_server_status(STATUS, connections=conns)

    assert sent == N
    # Regression: before the fix every connection rendered its own copy
    # (len(rendered) == N); the whole broadcast needs only one rendering.
    assert len(rendered) == 1, (
        f"SERVER_STATUS rendered {len(rendered)} times for {N} recipients; "
        "identical payload must be rendered once per broadcast"
    )

    # Safety invariants are unchanged: every recipient still gets the frame,
    # the rendered ``d`` is correct, and the one rendering is reused verbatim.
    payloads = [c.send_json.call_args[0][0] for c in conns]
    for p in payloads:
        assert p["op"] == int(GatewayOpcode.SERVER_STATUS)
        assert p["d"]["server_id"] == "1234567890123456789"
        assert p["d"]["channel_id"] == "9876543210987654321"
        assert p["d"]["nested"]["guild_id"] == "555555555555555555"
        assert p["d"]["nested"]["roles"] == [1, 2, 3, 4, 5]
        # Non-id numerics keep their type (the stringify backstop contract).
        assert p["d"]["closing_in_seconds"] == 2.0
        assert isinstance(p["d"]["closing_in_seconds"], float)
    assert all(p["d"] is payloads[0]["d"] for p in payloads)
    # The whole frame is identical across recipients (no per-connection field).
    assert all(p is payloads[0] for p in payloads)


@pytest.mark.asyncio
async def test_server_status_render_count_benchmark():
    """Observability helper: print renderer call count + wall time for -s runs."""
    sm = MagicMock()
    dispatcher = GatewayDispatcher(session_manager=sm)
    conns = _connections(N)

    real = disp.stringify_snowflake_ids
    calls = [0]

    def _counting(data):
        calls[0] += 1
        return real(data)

    t0 = time.perf_counter()
    with patch.object(disp, "stringify_snowflake_ids", side_effect=_counting):
        await dispatcher.broadcast_server_status(STATUS, connections=conns)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(
        f"\n[SERVER_STATUS BENCH] recipients={N} renderer_calls={calls[0]} "
        f"elapsed_ms={elapsed_ms:.2f}"
    )
    assert calls[0] == 1
