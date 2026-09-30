"""Bounded condition waits for tests that must observe a background effect.

A test that has to wait for a worker thread's first cycle, an executor-driven
database update, or a background logger to emit a line must not guess at it
with a fixed ``time.sleep``. Under ``pytest-xdist`` the host is oversubscribed,
so a sleep tuned on an idle machine becomes a flake on a loaded CI runner --
the effect is simply later than the guessed budget.

``wait_until`` polls the observable condition instead. It returns the instant
the condition is true (so the happy path is as fast as the effect itself) and
fails with the caller's message if a generous bounded timeout elapses, making a
genuinely missing effect a clear assertion failure rather than a stall.

``wait_until_async`` is the same contract for an ``asyncio`` event loop: the
predicate may be sync or a coroutine, and the wait yields to the loop between
polls (via ``asyncio.sleep``) instead of blocking its thread.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Callable, TypeVar, Union

__all__ = ["wait_until", "wait_until_async"]

_T = TypeVar("_T")

# Generous defaults: long enough that a legitimately slow CI runner still
# passes, short enough that a real hang fails quickly instead of stalling the
# suite. Both are overridable per call.
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.01

# A fixed failure message, or a zero-argument callable that builds one lazily.
# The callable form is rendered only when the timeout is actually reached, so
# it can read live state (a peer's ICE state, a collector's frame count) at the
# moment of failure instead of at call time.
Message = Union[str, Callable[[], str], None]


def _timeout_message(message: Message, timeout: float) -> str:
    """Render ``message`` (fixed or lazy) for a timeout failure."""
    if callable(message):
        return message()
    return message or f"condition not met within {timeout:g}s"


def wait_until(
    predicate: Callable[[], _T],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    message: Message = None,
) -> _T:
    """Poll ``predicate`` until it returns a truthy value or ``timeout`` elapses.

    Returns the first truthy value. Raises ``AssertionError`` (carrying
    ``message`` when given) if the timeout is reached first, so a missing
    background effect surfaces as a test assertion instead of a bare timeout.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(_timeout_message(message, timeout))
        time.sleep(interval)


async def wait_until_async(
    predicate: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    message: Message = None,
) -> Any:
    """Async twin of :func:`wait_until` for effects observed on an event loop.

    The predicate may be a plain callable or a coroutine function; an awaitable
    result is awaited automatically. Between polls the wait yields to the loop
    with ``asyncio.sleep`` rather than blocking, so timers, transports and
    background tasks keep making progress. Returns the first truthy value and
    raises ``AssertionError`` (carrying ``message``) on timeout, exactly like
    the synchronous version.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if inspect.isawaitable(value):
            value = await value
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(_timeout_message(message, timeout))
        await asyncio.sleep(interval)
