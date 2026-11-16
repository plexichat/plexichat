"""Coalesced outbound frame batching: throughput and back-pressure tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.batching import OutboundFrameBatcher
from src.api.websocket.connection import Connection, ConnectionState


class _Recorder:
    """Captures the batches handed to the batcher's writer."""

    def __init__(self):
        self.batches = []

    async def write(self, frames):
        self.batches.append(list(frames))

    @property
    def frames(self):
        return [frame for batch in self.batches for frame in batch]


@pytest.mark.asyncio
async def test_flush_once_coalesces_many_frames_into_one_writer_call():
    recorder = _Recorder()
    batcher = OutboundFrameBatcher(recorder.write, max_batch=64)

    for i in range(12):
        assert batcher.enqueue({"op": 1, "s": i}) is True

    written = await batcher.flush_once()

    assert written == 12
    assert len(recorder.batches) == 1
    assert [f["s"] for f in recorder.frames] == list(range(12))
    assert batcher.stats()["flush_calls"] == 1
    assert batcher.stats()["flushed_frames"] == 12
    assert batcher.pending == 0


@pytest.mark.asyncio
async def test_flush_once_respects_max_batch():
    recorder = _Recorder()
    batcher = OutboundFrameBatcher(recorder.write, max_batch=4)

    for i in range(10):
        batcher.enqueue({"s": i})

    first = await batcher.flush_once()
    second = await batcher.flush_once()
    third = await batcher.flush_once()

    assert (first, second, third) == (4, 4, 2)
    assert [len(batch) for batch in recorder.batches] == [4, 4, 2]
    assert [f["s"] for f in recorder.frames] == list(range(10))
    assert batcher.pending == 0


@pytest.mark.asyncio
async def test_bounded_queue_sheds_frames_and_counts_backpressure():
    recorder = _Recorder()
    batcher = OutboundFrameBatcher(recorder.write, queue_size=3, max_batch=16)

    assert batcher.enqueue({"s": 1}) is True
    assert batcher.enqueue({"s": 2}) is True
    assert batcher.enqueue({"s": 3}) is True
    # The queue is now at its bound: further frames are shed, not buffered.
    assert batcher.enqueue({"s": 4}) is False
    assert batcher.enqueue({"s": 5}) is False

    assert batcher.queue_size == 3
    assert batcher.pending == 3
    assert batcher.stats()["dropped_frames"] == 2

    await batcher.flush_once()
    assert [f["s"] for f in recorder.frames] == [1, 2, 3]


@pytest.mark.asyncio
async def test_run_loop_flushes_on_interval_and_preserves_order():
    recorder = _Recorder()
    batcher = OutboundFrameBatcher(recorder.write, flush_interval=0.005, max_batch=100)
    task = asyncio.create_task(batcher.run())
    try:
        for i in range(30):
            batcher.enqueue({"s": i})
        # The timer (not an explicit flush) must drain the queue.
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert [f["s"] for f in recorder.frames] == list(range(30))
    assert len(recorder.batches) < 30  # coalesced, not one write per frame
    assert batcher.pending == 0


@pytest.mark.asyncio
async def test_connection_batching_writes_frames_in_order_on_flush():
    ws = AsyncMock()
    connection = Connection(websocket=ws, connection_id="c1")
    connection.state = ConnectionState.READY

    # Direct path (batching not enabled) writes through immediately.
    assert await connection.send_json({"op": 1, "s": 0}) is True
    ws.send_json.assert_awaited_once_with({"op": 1, "s": 0})

    ws.send_json.reset_mock()
    connection.enable_batching(queue_size=32, max_batch=16)
    for i in range(1, 6):
        assert await connection.send_json({"op": 1, "s": i}) is True
    # Enqueued frames are not on the wire yet.
    ws.send_json.assert_not_awaited()

    await connection.flush_outbound()
    assert [call.args[0]["s"] for call in ws.send_json.await_args_list] == [
        1,
        2,
        3,
        4,
        5,
    ]

    await connection.stop_batching()
    assert connection.outbound_batcher is None


@pytest.mark.asyncio
async def test_connection_batching_reports_saturation_and_stops_cleanly():
    ws = AsyncMock()
    connection = Connection(websocket=ws, connection_id="c2")
    connection.state = ConnectionState.READY
    batcher = connection.enable_batching(queue_size=2, max_batch=2)

    assert await connection.send_json({"s": 1}) is True
    assert await connection.send_json({"s": 2}) is True
    assert await connection.send_json({"s": 3}) is False  # shed
    assert batcher.stats()["dropped_frames"] == 1

    # stop_batching performs a best-effort final drain of accepted frames.
    await connection.stop_batching()
    assert [call.args[0]["s"] for call in ws.send_json.await_args_list] == [1, 2]
