"""Regression test for finding C7: deprecated get_event_loop loop binding.

Pre-fix, ``GatewayDispatcher.on_event`` (and its ``set_event_loop`` caller in
``gateway.py``) used ``asyncio.get_event_loop()``. Outside a running loop that
call is deprecated (DeprecationWarning on 3.10+, removal slated later) and --
depending on startup ordering -- creates/returns a *detached* loop, so the
dispatcher binds to the wrong loop and events are never dispatched.

Post-fix the binding path uses ``asyncio.get_running_loop()`` (fail-clearly
semantics) with lazy fallback to the explicitly bound loop, and never touches
``asyncio.get_event_loop()``.
"""

import asyncio
import threading
import warnings
from typing import cast
from unittest.mock import patch

import pytest

from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.events.models import Event
from src.core.events.types import EventType


def _sample_event() -> Event:
    return Event(event_type=EventType.MESSAGE_CREATE, data={"content": "hi"})


def test_on_event_no_running_loop_never_touches_deprecated_get_event_loop(
    dispatcher: GatewayDispatcher,
):
    """With no running loop and no bound loop, on_event must not call the
    deprecated asyncio.get_event_loop() (which would emit DeprecationWarning
    and/or manufacture a detached loop) and must not raise."""
    # Fresh dispatcher: no loop bound via set_event_loop.
    assert dispatcher._loop is None

    with warnings.catch_warnings():
        # Pre-fix this raises DeprecationWarning ("There is no current event
        # loop") out of asyncio.get_event_loop(); post-fix no such call
        # happens at all.
        warnings.simplefilter("error", DeprecationWarning)
        with patch(
            "asyncio.get_event_loop",
            side_effect=AssertionError("deprecated get_event_loop must not be called"),
        ):
            # Must degrade gracefully (drop + warn) instead of binding a
            # detached loop or crashing the event-callback thread.
            dispatcher.on_event(_sample_event(), [12345])


def test_on_event_no_running_loop_creates_no_detached_loop(
    dispatcher: GatewayDispatcher, session_manager
):
    """The no-loop path must not install a stray current loop for this thread."""
    # asyncio.get_event_loop_policy() is deprecated on 3.14+ (slated for
    # removal in 3.16). The policy object is only used here to observe --
    # never manufacture -- the thread-local current loop.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        policy = asyncio.get_event_loop_policy()

    def _current_or_none():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                return policy.get_event_loop()
            except RuntimeError:
                return None

    before = _current_or_none()
    dispatcher.on_event(_sample_event(), [12345])
    after = _current_or_none()
    # Pre-fix, get_event_loop() manufactured a fresh detached loop here, so
    # `after` would be a new loop object differing from `before`.
    assert after is before, "on_event must not manufacture a detached current loop"


@pytest.mark.asyncio
async def test_on_event_inside_running_loop_targets_running_loop(
    dispatcher: GatewayDispatcher,
):
    """When a loop is running, on_event must schedule onto that exact loop --
    never a detached one -- with no behavior change vs. a bound loop."""
    running = asyncio.get_running_loop()
    captured = {}

    def fake_submit(coro, loop):
        captured["loop"] = loop
        coro.close()
        future = running.create_future()

        def _cancelled(_fut):
            pass

        future.add_done_callback(_cancelled)
        future.cancel()
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=fake_submit):
        dispatcher.on_event(_sample_event(), [12345])

    assert captured.get("loop") is running


@pytest.mark.asyncio
async def test_gateway_binding_uses_running_loop(session_manager):
    """The gateway's set_event_loop call site must bind the running loop via
    get_running_loop(), not the deprecated get_event_loop()."""
    import pathlib

    gateway_src = (
        pathlib.Path(__file__).resolve().parents[2] / "api" / "websocket" / "gateway.py"
    )
    # tests live at src/tests/websocket/ -> parents[2] == src/
    text = gateway_src.read_text()
    assert "dispatcher.set_event_loop(asyncio.get_running_loop())" in text
    assert "dispatcher.set_event_loop(asyncio.get_event_loop())" not in text

    # And the binding itself targets the actual running loop.
    running = asyncio.get_running_loop()
    local = GatewayDispatcher(session_manager=session_manager)
    local.set_event_loop(asyncio.get_running_loop())
    assert local._loop is running


def test_dispatcher_source_free_of_deprecated_get_event_loop():
    """Guard: dispatcher.py must not call asyncio.get_event_loop() (comments
    mentioning it are fine; only real calls count)."""
    import pathlib
    import re

    dispatcher_src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "api"
        / "websocket"
        / "dispatcher.py"
    )
    call_sites = [
        line
        for line in dispatcher_src.read_text().splitlines()
        if re.search(r"(?<!\w)asyncio\.get_event_loop\s*\(", line)
        and line.lstrip()[:1] != "#"
    ]
    assert call_sites == [], f"deprecated get_event_loop calls remain: {call_sites}"


def test_websocket_package_free_of_deprecated_get_event_loop():
    """Guard: no real asyncio.get_event_loop() call may remain anywhere in
    the websocket package (dispatcher, gateway binding, listener clocks).

    Pre-fix this fails on gateway.py's set_event_loop call site and the
    cross-worker listener clocks in __init__.py; post-fix it passes.
    Comments mentioning the name are fine; only real calls count."""
    import pathlib
    import re

    package_dir = pathlib.Path(__file__).resolve().parents[2] / "api" / "websocket"
    offenders = []
    for src_file in sorted(package_dir.glob("*.py")):
        for lineno, line in enumerate(src_file.read_text().splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if re.search(r"(?<!\w)asyncio\.get_event_loop\s*\(", line):
                offenders.append(f"{src_file.name}:{lineno}: {stripped}")
    assert offenders == [], f"deprecated get_event_loop calls remain: {offenders}"


def test_on_event_from_foreign_thread_uses_bound_loop(
    dispatcher: GatewayDispatcher, session_manager
):
    """Events arriving on a non-loop thread (event-bus callback case) must go
    to the explicitly bound loop, not a per-thread detached loop."""
    captured = {}

    class FakeLoop:
        def is_closed(self):
            return False

    fake = FakeLoop()
    dispatcher.set_event_loop(cast(asyncio.AbstractEventLoop, fake))

    def fake_submit(coro, loop):
        captured["loop"] = loop
        coro.close()

        fut = object()
        return fut

    errors = []

    def worker():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                with patch("asyncio.run_coroutine_threadsafe", side_effect=fake_submit):
                    dispatcher.on_event(_sample_event(), [12345])
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)

    assert not errors, f"on_event raised from foreign thread: {errors}"
    assert captured.get("loop") is fake
