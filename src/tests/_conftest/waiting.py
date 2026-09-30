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
"""

from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

__all__ = ["wait_until"]

_T = TypeVar("_T")

# Generous defaults: long enough that a legitimately slow CI runner still
# passes, short enough that a real hang fails quickly instead of stalling the
# suite. Both are overridable per call.
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.01


def wait_until(
    predicate: Callable[[], _T],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    message: Optional[str] = None,
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
            raise AssertionError(message or f"condition not met within {timeout:g}s")
        time.sleep(interval)
