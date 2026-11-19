"""Scaling regressions for transcription background-job scheduling.

A burst of call-end events used to spawn one unbounded ``asyncio.create_task``
per event on the async path, with no dedupe and no deadline. These tests pin
the bounded queue, call-id dedupe, concurrency ceiling and per-job timeout.
"""

import asyncio
import queue
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.artifacts.transcription import worker as w


@pytest.fixture
def isolated_scheduler(monkeypatch):
    """Fresh bounded queue + dedupe set; suppress the automatic drainer."""
    monkeypatch.setattr(w, "_QUEUE", queue.Queue(maxsize=w._QUEUE_MAX))
    monkeypatch.setattr(w, "_INFLIGHT", set())
    # Scheduling calls ensure_transcription_drainer(); flag it as already
    # draining so the tests drive _drain_queue() explicitly and deterministically.
    monkeypatch.setattr(w, "_QUEUE_DRAINING", True)


@pytest.mark.asyncio
async def test_duplicate_call_ids_deduped_queued_and_running(isolated_scheduler):
    """Repeated scheduling of the same call occupies a single slot."""
    with patch.object(w, "transcribe_call", new_callable=AsyncMock) as mocked:
        for _ in range(20):
            w.schedule_transcribe_call(123, Mock(), {})

        assert w._get_queue().qsize() == 1
        assert set(w._INFLIGHT) == {123}

        await w._drain_queue()
        assert mocked.await_count == 1
        assert w._INFLIGHT == set()
        assert w._get_queue().empty()


@pytest.mark.asyncio
async def test_duplicate_while_job_running_is_shed(isolated_scheduler):
    """A second schedule for an in-flight call is dropped, not queued twice."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def blocked(call_id, db, cfg):
        calls.append(call_id)
        started.set()
        await release.wait()

    with patch.object(
        w, "transcribe_call", new_callable=AsyncMock, side_effect=blocked
    ):
        w.schedule_transcribe_call(77, Mock(), {})
        drainer = asyncio.ensure_future(w._drain_queue())
        await asyncio.wait_for(started.wait(), 1.0)

        w.schedule_transcribe_call(77, Mock(), {})
        assert w._get_queue().qsize() == 0
        assert set(w._INFLIGHT) == {77}

        release.set()
        await asyncio.wait_for(drainer, 1.0)

    assert calls == [77]
    assert w._INFLIGHT == set()


@pytest.mark.asyncio
async def test_bounded_queue_sheds_excess_jobs(isolated_scheduler, monkeypatch):
    """Overload drops jobs instead of growing the queue without limit."""
    monkeypatch.setattr(w, "_QUEUE_MAX", 3)
    monkeypatch.setattr(w, "_QUEUE", queue.Queue(maxsize=3))

    with patch.object(w, "transcribe_call", new_callable=AsyncMock):
        for call_id in range(10):
            w.schedule_transcribe_call(call_id, Mock(), {})

        assert w._get_queue().qsize() == 3
        assert set(w._INFLIGHT) == {0, 1, 2}


@pytest.mark.asyncio
async def test_concurrency_cap_and_per_job_timeout(isolated_scheduler, monkeypatch):
    """No more than N provider jobs run at once; a hung job is timed out."""
    monkeypatch.setattr(w, "_MAX_ACTIVE", 2)
    monkeypatch.setattr(w, "_JOB_TIMEOUT", 0.05)
    monkeypatch.setattr(w, "_DRAIN_POLL_INTERVAL", 0.01)

    active = 0
    peak = 0
    calls = []

    async def slow(call_id, db, cfg):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append(call_id)
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        finally:
            active -= 1

    with patch.object(w, "transcribe_call", new_callable=AsyncMock, side_effect=slow):
        for call_id in range(5):
            w.schedule_transcribe_call(call_id, Mock(), {})

        await asyncio.wait_for(w._drain_queue(), 2.0)

    assert 1 <= peak <= 2
    assert sorted(calls) == [0, 1, 2, 3, 4]
    assert w._get_queue().empty()
    assert w._INFLIGHT == set()
