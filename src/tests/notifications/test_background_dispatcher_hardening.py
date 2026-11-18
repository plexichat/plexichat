"""Regression tests for background dispatcher scaling hardening.

Covers the three load weaknesses fixed in ``notifications.background``:
duplicate jobs for the same logical work, a timed-out/hung job stalling the
single consumer and piling up the queue, and an uncapped number of live job
threads.
"""

import threading
import time

import pytest

from src.core.notifications.background import BoundedBackgroundDispatcher


def _wait_for(predicate, timeout: float = 2.0, interval: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def dispatcher():
    instance = BoundedBackgroundDispatcher(
        "test-dispatch",
        queue_bound=4,
        job_timeout=0.2,
        max_active_jobs=2,
    )
    yield instance
    instance.drain(timeout=1.0)


def test_duplicate_key_is_shed(dispatcher):
    calls = []
    running = threading.Event()
    release = threading.Event()

    def first_job():
        calls.append("first")
        running.set()
        release.wait(2)

    assert dispatcher.submit(first_job, key=("msg", 1)) is True
    assert running.wait(1)
    # The first job is still pending; the same key must not enqueue again.
    assert dispatcher.submit(lambda: calls.append("dup"), key=("msg", 1)) is False
    release.set()
    assert _wait_for(
        lambda: dispatcher.pending() == 0 and dispatcher.active_jobs() == 0
    )
    assert calls == ["first"]


def test_key_released_after_completion(dispatcher):
    finished = threading.Event()

    def job():
        finished.set()

    assert dispatcher.submit(job, key="reusable") is True
    assert finished.wait(1)
    assert _wait_for(lambda: dispatcher.active_jobs() == 0)
    assert dispatcher.submit(job, key="reusable") is True
    assert _wait_for(
        lambda: dispatcher.pending() == 0 and dispatcher.active_jobs() == 0
    )


def test_queue_bound_is_enforced(monkeypatch):
    instance = BoundedBackgroundDispatcher("test-bound", queue_bound=2)
    # Keep the consumer dormant so the bound is observable deterministically.
    monkeypatch.setattr(instance, "_ensure_worker", lambda: None)
    assert instance.submit(lambda: None) is True
    assert instance.submit(lambda: None) is True
    assert instance.submit(lambda: None) is False
    assert instance.pending() == 2


def test_slow_job_does_not_block_consumer(dispatcher):
    started = threading.Event()
    release = threading.Event()
    second_ran = threading.Event()

    def slow_job():
        started.set()
        release.wait(2)

    def fast_job():
        second_ran.set()

    assert dispatcher.submit(slow_job, key="slow") is True
    assert started.wait(1)
    submit_time = time.monotonic()
    assert dispatcher.submit(fast_job, key="fast") is True
    # The consumer must move past the over-deadline job instead of piling up.
    assert second_ran.wait(1)
    assert time.monotonic() - submit_time < 1.0
    release.set()


def test_dispatcher_reusable_after_drain(dispatcher):
    ran = threading.Event()

    assert dispatcher.submit(lambda: ran.set(), key="reuse") is True
    assert ran.wait(1)
    dispatcher.drain(timeout=1.0)

    ran.clear()
    assert dispatcher.submit(lambda: ran.set(), key="reuse") is True
    assert ran.wait(1)


def test_active_job_cap_enforces_backpressure():
    release = threading.Event()
    started = []
    guard = threading.Lock()
    instance = BoundedBackgroundDispatcher(
        "test-cap",
        queue_bound=8,
        job_timeout=0.05,
        max_active_jobs=1,
    )

    def hanging_job():
        with guard:
            started.append(1)
        release.wait(2)

    assert instance.submit(hanging_job, key="a") is True
    assert _wait_for(lambda: len(started) == 1)
    assert instance.submit(hanging_job, key="b") is True
    # The second job is shed at execution time because the cap is reached.
    assert _wait_for(lambda: instance.pending() == 0)
    assert len(started) == 1
    release.set()
    instance.drain(timeout=1.0)
