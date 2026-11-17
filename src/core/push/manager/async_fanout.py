"""Generic bounded async fan-out with backpressure.

A push burst (one notification delivered to many users, or one user with many
devices) must not create an unbounded number of tasks, and a producer must not
be able to enqueue work faster than the senders can drain it. This module
provides the bounded queue + fixed worker pool used by the push delivery
mixin.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Iterable, List, Optional, TypeVar

DEFAULT_CONCURRENCY = 8
DEFAULT_QUEUE_SIZE = 64
DEFAULT_ITEM_TIMEOUT_S = 10.0

Item = TypeVar("Item")
Result = TypeVar("Result")

_WORKER_STOP = object()


async def bounded_fanout(
    items: Iterable[Item],
    worker: Callable[[Item], Awaitable[Result]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_queue: int = DEFAULT_QUEUE_SIZE,
    timeout: float = DEFAULT_ITEM_TIMEOUT_S,
) -> List[Optional[Result]]:
    """Run ``worker`` over ``items`` with bounded in-flight work.

    ``max_queue`` bounds pending items: the producer awaits ``queue.put`` once
    the queue is full, so a slow consumer applies backpressure instead of
    accumulating unbounded work. Exactly ``concurrency`` worker coroutines run
    at a time. Results are returned in input order; a failed or timed-out item
    contributes ``None`` so one bad target cannot abort the whole fan-out.
    """
    workers = max(1, int(concurrency))
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max(1, int(max_queue)))
    results: List[Optional[Result]] = []

    async def consume() -> None:
        while True:
            job = await queue.get()
            try:
                if job is _WORKER_STOP:
                    return
                index, item = job
                try:
                    value = await asyncio.wait_for(
                        worker(item), max(0.001, float(timeout))
                    )
                except Exception:
                    # Isolated failure: one unreachable device/user must not
                    # stop delivery to the rest of the batch.
                    value = None
                if index >= len(results):
                    results.extend([None] * (index + 1 - len(results)))
                results[index] = value
            finally:
                queue.task_done()

    consumers = [asyncio.create_task(consume()) for _ in range(workers)]
    try:
        # Stream the input: the producer blocks on a full queue, so generating
        # work cannot outrun the workers (bounded memory, real backpressure).
        for index, item in enumerate(items):
            await queue.put((index, item))  # backpressure at the queue bound
        for _ in consumers:
            await queue.put(_WORKER_STOP)
        await asyncio.gather(*consumers)
    finally:
        for task in consumers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
    return results
