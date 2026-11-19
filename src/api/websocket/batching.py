"""Coalesced outbound frame batching for gateway connections.

Why this exists
---------------
Before: every dispatched event called :meth:`Connection.send_json`, which took
the per-connection send lock and performed a single ASGI send immediately. A
burst of N events (typing indicators, presence updates, artifact operations,
resume replay) produced N lock acquisitions and N event-loop round trips for
every recipient connection. A slow consumer had no queue at all, so pressure
translates directly into scheduler churn.

After: an opt-in :class:`OutboundFrameBatcher` collects frames in a bounded
``asyncio.Queue``. A single background task drains up to ``max_batch`` frames
per flush and writes them through one writer (one lock acquisition) on a fixed
flush interval, waking early when the first frame of an idle period arrives.
When the queue is full, new frames are shed and counted instead of growing
memory without bound. Coalescing removes per-frame scheduling/lock overhead
without changing the wire protocol: each queued frame is still an independent
WebSocket message, written in enqueue order.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, List, Optional

import utils.logger as logger

#: Default ceiling on queued (not yet flushed) frames per connection.
DEFAULT_QUEUE_SIZE = 256

#: How long the flush task waits for the first frame of an idle period. Bounds
#: the added latency of batching; a burst is flushed as soon as it is enqueued.
DEFAULT_FLUSH_INTERVAL_S = 0.01

#: Most frames written by a single writer call (flush). Bounds the time spent
#: in one flush so a large backlog cannot starve other loop work.
DEFAULT_MAX_BATCH = 128

#: Upper bound on a single ASGI ``send`` for one connection. A consumer that
#: stops reading must never pin the per-connection writer (and therefore the
#: batch flush task) forever; the timed-out frame is dropped, not retried.
DEFAULT_SEND_TIMEOUT_S = 5.0

#: Upper bound on a single :meth:`OutboundFrameBatcher.drain` call, used by
#: connection teardown and shutdown. Without a deadline a peer that has
#: stopped reading could pin ``flush_outbound``/``stop_batching`` forever,
#: leaking the serving coroutine and every object it retains.
DEFAULT_DRAIN_TIMEOUT_S = 5.0


Frame = Dict[str, object]
# Writers may report a status (e.g. bool success); the batcher ignores it.
FrameWriter = Callable[[List[Frame]], Awaitable[object]]


class OutboundFrameBatcher:
    """Bounded queue + timer that coalesces outbound frames into flush batches.

    ``writer`` receives a list of frames and is responsible for writing them
    (in order) to the transport under whatever locking the transport needs.
    The batcher owns only scheduling and back-pressure accounting; it knows
    nothing about WebSockets, which keeps it directly testable.
    """

    def __init__(
        self,
        writer: FrameWriter,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL_S,
        max_batch: int = DEFAULT_MAX_BATCH,
    ) -> None:
        self._queue: asyncio.Queue[Frame] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._writer = writer
        self._flush_interval = max(0.001, float(flush_interval))
        self._max_batch = max(1, int(max_batch))
        self._wakeup = asyncio.Event()

        # Back-pressure / throughput counters (cumulative since construction).
        self._dropped_frames = 0
        self._flushed_frames = 0
        self._flush_calls = 0
        self._last_batch_size = 0

    @property
    def pending(self) -> int:
        """Number of frames currently queued for flush."""
        return self._queue.qsize()

    @property
    def queue_size(self) -> int:
        """Configured bound on queued frames."""
        return self._queue.maxsize

    def stats(self) -> Dict[str, int]:
        """Return a snapshot of back-pressure/throughput counters."""
        return {
            "queued": self._queue.qsize(),
            "queue_size": self._queue.maxsize,
            "dropped_frames": self._dropped_frames,
            "flushed_frames": self._flushed_frames,
            "flush_calls": self._flush_calls,
            "last_batch_size": self._last_batch_size,
        }

    def enqueue(self, frame: Frame) -> bool:
        """Queue one frame without blocking.

        Returns ``True`` when accepted. A full queue sheds the frame and
        increments :attr:`_dropped_frames` so saturation is observable instead
        of silently consuming unbounded memory.
        """
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._dropped_frames += 1
            if self._dropped_frames == 1:
                logger.warning(
                    "Outbound frame queue full (capacity=%d); "
                    "shedding frames until the consumer catches up",
                    self._queue.maxsize,
                )
            return False
        self._wakeup.set()
        return True

    async def flush_once(self) -> int:
        """Write one batch now; return the number of frames in that batch."""
        batch: List[Frame] = []
        while len(batch) < self._max_batch:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return 0

        self._flush_calls += 1
        self._last_batch_size = len(batch)
        try:
            await self._writer(batch)
        except Exception as exc:  # a failed write must not kill the flush task
            logger.debug(f"Outbound frame batch write failed: {exc}")
        else:
            self._flushed_frames += len(batch)
        finally:
            for _ in batch:
                self._queue.task_done()
        return len(batch)

    def _shed_queued(self) -> int:
        """Drop every still-queued frame and count it as back-pressure.

        Called when a drain hits its deadline (a stalled consumer). Frames that
        were already handed to the writer in the current batch are gone from
        the queue; this only accounts for the ones we have not started.
        """
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
            dropped += 1
        if dropped:
            self._dropped_frames += dropped
            self._wakeup.clear()
            logger.warning(
                "Outbound drain deadline reached; shedding %d queued frame(s) "
                "for a slow consumer",
                dropped,
            )
        return dropped

    async def drain(self, timeout: Optional[float] = DEFAULT_DRAIN_TIMEOUT_S) -> int:
        """Flush the queue, then stop at ``timeout`` and shed the remainder.

        A ``None`` timeout restores the old unbounded behaviour for callers that
        deliberately want it; every production caller uses the finite default.
        Each flush is itself bounded by the remaining budget, so a writer that
        stalls (or ignores its own per-send timeout) cannot defeat the deadline;
        frames still queued when the budget is exhausted are shed and counted.
        """
        total = 0
        loop = asyncio.get_running_loop()
        deadline = None
        if timeout is not None:
            deadline = loop.time() + max(0.0, float(timeout))
        while True:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                self._shed_queued()
                return total
            try:
                # Bound each flush too, so a writer that ignores its own
                # timeout cannot defeat the drain deadline.
                written = await asyncio.wait_for(self.flush_once(), timeout=remaining)
            except asyncio.TimeoutError:
                self._shed_queued()
                return total
            if written == 0:
                return total
            total += written

    async def run(self) -> None:
        """Background loop: flush on the interval or on the first queued frame."""
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        self._wakeup.wait(), timeout=self._flush_interval
                    )
                except asyncio.TimeoutError:
                    pass
                self._wakeup.clear()
                await self.flush_once()
        except asyncio.CancelledError:
            # Best-effort final drain so a clean shutdown does not strand
            # already-accepted frames (e.g. the final SERVER_STATUS frame).
            # Bounded by the default drain deadline so cancellation cannot
            # itself hang on a stalled consumer.
            try:
                await self.drain()
            except Exception:  # pragma: no cover - defensive on shutdown
                pass
            raise
