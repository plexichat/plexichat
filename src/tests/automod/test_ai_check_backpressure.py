"""Regression tests for bounded phase-2 AI moderation scheduling.

``schedule_ai_check`` used to spawn one daemon thread per message send/edit.
These tests simulate a burst of scheduling calls and assert the four scaling
properties now provided by the bounded dispatcher: a fixed queue bound,
key-based de-duplication, per-job timeouts that cannot stall the consumer, and
a ceiling on concurrently live job threads. Phase-1 synchronous moderation is
untouched; these only cover the best-effort async path.
"""

import threading
import time

import pytest

from src.core import automod
from src.core.notifications.background import BoundedBackgroundDispatcher


def _wait_for(predicate, timeout: float = 3.0, interval: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _schedule(message_id: int, content: str = "body"):
    return automod.schedule_ai_check(
        server_id=100,
        channel_id=200,
        user_id=300,
        message_id=message_id,
        content=content,
        context={"source": "message_create"},
    )


@pytest.fixture
def small_dispatcher(monkeypatch):
    """Install a small bounded dispatcher and a controllable job stub."""
    release = threading.Event()
    calls = []

    def fake_run(server_id, channel_id, user_id, message_id, content, ctx):
        calls.append((message_id, content, ctx))
        release.wait(2)

    dispatcher = BoundedBackgroundDispatcher(
        "automod-test", queue_bound=2, job_timeout=0.2, max_active_jobs=2
    )
    monkeypatch.setattr(automod, "_ai_dispatcher", dispatcher)
    monkeypatch.setattr(automod, "_run_ai_check", fake_run)
    # schedule_ai_check submits with an explicit deadline; shrink it so a hung
    # stub cannot stall the consumer for the production 30s.
    monkeypatch.setattr(automod, "AI_CHECK_JOB_TIMEOUT", 0.2)
    yield dispatcher, calls, release
    release.set()
    dispatcher.drain(timeout=1.0)


def test_burst_is_bounded_and_duplicates_are_deduped(small_dispatcher):
    """A flood of distinct jobs cannot exceed the queue bound; duplicates shed."""
    dispatcher, calls, release = small_dispatcher
    # Keep the consumer dormant so the bound and dedupe are deterministic.
    original_ensure = dispatcher._ensure_worker
    dispatcher._ensure_worker = lambda: None
    try:
        accepted = [_schedule(i) for i in range(50)]
        assert accepted.count(True) == dispatcher._queue.maxsize == 2

        # Re-scheduling a still-pending job (same id + content) is deduped.
        assert _schedule(0) is False
        # New content for the same message is a distinct key, but the queue
        # is full so it is still shed rather than growing memory.
        assert _schedule(0, "revised") is False
        assert dispatcher.pending() == 2
    finally:
        # Let the now-populated queue drain under the real consumer.
        dispatcher._ensure_worker = original_ensure
        dispatcher._ensure_worker()

    release.set()
    assert _wait_for(
        lambda: dispatcher.pending() == 0 and dispatcher.active_jobs() == 0
    )
    # Only the two queued jobs executed; the 48 overflow + duplicates never ran.
    assert len(calls) == 2
    assert {c[0] for c in calls} <= {0, 1}


def test_active_job_cap_sheds_load_without_stalling(small_dispatcher):
    """Hung AI jobs are timed out so the consumer keeps shedding, not piling up."""
    dispatcher, calls, release = small_dispatcher
    # Widen the queue so shedding comes from the concurrency cap, not fullness.
    dispatcher._queue = __import__("queue").Queue(maxsize=64)

    for i in range(20):
        _schedule(i)

    # The consumer moves past the two blocked jobs (0.2s timeout) and drops
    # the rest at the active-job cap instead of blocking forever.
    assert _wait_for(lambda: dispatcher.pending() == 0)
    assert dispatcher.active_jobs() <= 2
    # Only the capped number of jobs ever started; the rest were shed.
    assert len(calls) == 2
    assert {c[0] for c in calls} <= {0, 1}


def test_scheduling_preserves_payload_and_context(small_dispatcher):
    """Accepted jobs still run the real body with system/automod context flags."""
    dispatcher, calls, release = small_dispatcher
    assert _schedule(42, "hello world") is True
    assert _wait_for(lambda: len(calls) == 1)
    message_id, content, ctx = calls[0]
    assert message_id == 42
    assert content == "hello world"
    assert ctx["source"] == "message_create"
    assert ctx["__automod_replacement__"] is True
    assert ctx["__system_context__"] is True
    assert ctx["__automod_caller__"] is True
