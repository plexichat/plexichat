"""Regression tests for outbound batching/coalescing optimizations.

Covers the three hot-path fixes:
  * identical compressed frames are serialized once, not once per frame;
  * the serialization cache is bounded (no unbounded growth on unique payloads);
  * the bounded gather window coalesces frames delivered across loop turns
    without weakening timeouts, size caps, or back-pressure shedding.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.batching import (
    DEFAULT_GATHER_WINDOW_S,
    OutboundFrameBatcher,
)
from src.api.websocket.connection import (
    MAX_SERIALIZE_CACHE_ENTRIES,
    Connection,
    ConnectionState,
)


@pytest.mark.asyncio
async def test_identical_compressed_frames_serialized_once(monkeypatch):
    """A burst of the same rendered frame object pays for one json.dumps."""
    ws = AsyncMock()
    conn = Connection(websocket=ws, connection_id="ident")
    conn.state = ConnectionState.READY
    conn.enable_compression()

    frame = {"op": 0, "d": {"user_id": "123456789012345678", "status": "idle"}}
    real_dumps = json.dumps
    calls = 0

    def counting_dumps(*args, **kwargs):
        nonlocal calls
        if args and args[0] is frame:
            calls += 1
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", counting_dumps)

    frames = [frame] * 100
    assert await conn._write_frames(frames) is True

    assert calls == 1  # was 100 before the identity cache
    assert len(conn._serialize_cache) == 1
    # Every frame is still an independent, correctly ordered binary message.
    assert ws.send_bytes.await_count == 100
    for call in ws.send_bytes.await_args_list:
        assert isinstance(call.args[0], bytes) and call.args[0]


def test_serialize_cache_is_bounded_fifo():
    """Unique frames cannot grow the cache past its bound; oldest is evicted."""
    conn = Connection(websocket=AsyncMock(), connection_id="bounds")
    total = MAX_SERIALIZE_CACHE_ENTRIES * 3 + 5
    for i in range(total):
        conn._serialize_frame({"s": i})

    assert len(conn._serialize_cache) == MAX_SERIALIZE_CACHE_ENTRIES
    # The cache holds the most recent frames, in FIFO insertion order. Assert
    # on values, not ids: an evicted object's id() may be recycled by CPython.
    assert [v[0]["s"] for v in conn._serialize_cache.values()] == list(
        range(total - MAX_SERIALIZE_CACHE_ENTRIES, total)
    )


@pytest.mark.asyncio
async def test_gather_window_coalesces_frames_across_loop_turns():
    """Frames delivered one per event-loop turn share one batch."""
    batches = []

    async def writer(frames):
        batches.append(list(frames))
        await asyncio.sleep(0)  # model an ASGI send yielding to the loop

    batcher = OutboundFrameBatcher(
        writer, queue_size=4096, max_batch=128, flush_interval=0.01
    )
    assert batcher._gather_window <= batcher._flush_interval
    assert batcher._gather_window <= DEFAULT_GATHER_WINDOW_S

    task = asyncio.create_task(batcher.run())
    try:
        for i in range(40):
            assert batcher.enqueue({"s": i}) is True
            await asyncio.sleep(0)
        # Let the gather window + flush complete.
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    ordered = [f["s"] for batch in batches for f in batch]
    assert ordered == list(range(40))  # no loss, order preserved
    assert len(batches) < 10  # was 40 (one flush per frame) before the fix


@pytest.mark.asyncio
async def test_gather_window_preserves_backpressure_and_drain_deadline():
    """The gather window must not reintroduce unbounded waits on a slow peer."""
    gate = asyncio.Event()

    async def stalled_writer(frames):
        await gate.wait()

    batcher = OutboundFrameBatcher(
        stalled_writer, queue_size=256, max_batch=128, flush_interval=0.01
    )
    for i in range(200):
        assert batcher.enqueue({"s": i}) is True

    start = time.monotonic()
    written = await asyncio.wait_for(batcher.drain(timeout=0.03), timeout=1.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert written == 0
    assert batcher.pending == 0
    # 128 frames were in the stalled batch; the other 72 are shed and counted.
    assert batcher.stats()["dropped_frames"] == 72
    gate.set()


@pytest.mark.asyncio
async def test_cancelling_run_with_gather_window_still_drains_and_settles():
    """Cancellation during the gather window is bounded and sheds backlog."""
    gate = asyncio.Event()

    async def stalled_writer(frames):
        await gate.wait()

    batcher = OutboundFrameBatcher(
        stalled_writer, queue_size=256, max_batch=128, flush_interval=0.01
    )
    task = asyncio.create_task(batcher.run())
    for i in range(64):
        assert batcher.enqueue({"s": i}) is True
    # Give the gather window a chance to elapse so a flush is in flight.
    await asyncio.sleep(0.01)

    start = time.monotonic()
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        gate.set()
        pytest.fail("run() did not settle after cancellation within 1s")
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert batcher.pending == 0  # final bounded drain shed the remainder
    gate.set()
