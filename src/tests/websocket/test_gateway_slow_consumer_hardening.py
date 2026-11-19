"""Scaling/DoS regression tests for stalled gateway consumers.

A peer that stops reading its socket must not be able to pin a connection's
writer, the coalescing flush task, or connection teardown forever. Before the
hardening, ``Connection._write_frames`` awaited an unbounded ASGI ``send`` and
``OutboundFrameBatcher.drain`` had no deadline, so a single slow consumer could
leak serving coroutines (and everything they retain) until process exhaustion.
"""

import asyncio
import time

import pytest

from src.api.websocket.batching import OutboundFrameBatcher
from src.api.websocket.connection import Connection, ConnectionState


class _StalledWebSocket:
    """ASGI-like socket whose sends never complete until explicitly released."""

    def __init__(self):
        self.release = asyncio.Event()
        self.send_cancelled = 0
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.send_cancelled += 1
            raise

    async def send_bytes(self, data):
        self.sent.append(data)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.send_cancelled += 1
            raise


@pytest.mark.asyncio
async def test_slow_consumer_send_times_out_instead_of_blocking_forever():
    """A stalled ASGI send is abandoned after the per-send timeout."""
    ws = _StalledWebSocket()
    connection = Connection(websocket=ws, connection_id="slow-1")
    connection.state = ConnectionState.READY
    connection._send_timeout = 0.05

    start = time.monotonic()
    # The outer wait_for is the test's own tripwire: without the fix the inner
    # send would hang and this assertion would time out.
    result = await asyncio.wait_for(
        connection._write_frames([{"op": 1, "s": 1}]), timeout=1.0
    )
    elapsed = time.monotonic() - start

    assert result is False
    assert connection._send_timeouts == 1
    assert ws.send_cancelled == 1
    assert elapsed < 1.0

    # A still-stalled socket never gets the frame written; release it only to
    # clean up the fake event.
    ws.release.set()


@pytest.mark.asyncio
async def test_slow_consumer_batch_write_abandons_remaining_frames():
    """One timed-out send must not walk the whole batch frame by frame."""
    ws = _StalledWebSocket()
    connection = Connection(websocket=ws, connection_id="slow-2")
    connection.state = ConnectionState.READY
    connection._send_timeout = 0.05

    start = time.monotonic()
    result = await asyncio.wait_for(
        connection._write_frames([{"s": i} for i in range(20)]), timeout=1.0
    )
    elapsed = time.monotonic() - start

    assert result is False
    # The first frame times out and the rest of the batch is abandoned, so the
    # writer attempted (and the peer was offered) exactly one frame.
    assert len(ws.sent) == 1
    assert connection._send_timeouts == 1
    assert elapsed < 1.0
    ws.release.set()


@pytest.mark.asyncio
async def test_drain_deadline_sheds_backlog_from_stalled_writer():
    """Drain stops at its deadline and sheds frames it could not hand off."""
    gate = asyncio.Event()

    async def stalled_writer(frames):
        await gate.wait()

    batcher = OutboundFrameBatcher(stalled_writer, queue_size=256, max_batch=128)
    for i in range(200):
        assert batcher.enqueue({"s": i}) is True

    start = time.monotonic()
    written = await asyncio.wait_for(batcher.drain(timeout=0.03), timeout=1.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert batcher.pending == 0
    # 128 frames were handed to the in-flight (cancelled) batch; the remaining
    # 72 were still queued and are shed + counted as back-pressure.
    assert written == 0
    assert batcher.stats()["dropped_frames"] == 72
    gate.set()


@pytest.mark.asyncio
async def test_connection_flush_outbound_is_bounded_on_stalled_socket():
    """Teardown's flush cannot hang even when every send stalls."""
    ws = _StalledWebSocket()
    connection = Connection(websocket=ws, connection_id="slow-3")
    connection.state = ConnectionState.READY
    connection._send_timeout = 0.05
    # Attach a batcher directly (no background run task) so the flush is
    # exercised deterministically by flush_outbound alone.
    batcher = OutboundFrameBatcher(connection._write_frames, max_batch=128)
    connection._outbound_batcher = batcher
    for i in range(200):
        assert await connection.send_json({"s": i}) is True

    start = time.monotonic()
    await asyncio.wait_for(connection.flush_outbound(), timeout=2.0)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0
    assert batcher.pending == 0
    assert connection._send_timeouts >= 1

    await connection.stop_batching()
    ws.release.set()
