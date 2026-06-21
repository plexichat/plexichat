"""
Phase-2 finalization: critical-subscriber + queue-recreate + loss-metrics tests.

Covers the new code paths in ``src/core/events/manager.py``:

* ``subscribe(cb, *, critical=False)`` -- second dict + merged-pool cap eviction
* ``dispatch`` critical sync-fanout BEFORE the queue.put_nowait
* Critical callbacks fire BOTH on the eager-path and on the QueueFull path
* Exception semantics: critical callbacks raising are logged + swallowed
* ``get_loss_metrics()`` shape -- ``queue: {size, max} | None`` pre-warm
* ``is_queue_recreate_pending(loop=None)`` -- audit predicate

The tests deliberately lean on the synchronous fallback inside ``dispatch``
(no running loop -> eager fan-out) so we don't have to introduce
pytest-asyncio markers everywhere; one asyncio-driven test is included to
exercise the QueueFull path that ONLY fires under a real running loop.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.core.events.models import Event
from src.core.events.types import EventType


pytestmark = pytest.mark.unit


# --- helpers -------------------------------------------------------------


def _make_em(events_module):
    """Return the live EventManager wired up by the events_module fixture."""
    return events_module._manager


def _stub_recipients(em, recipients=None):
    """Bypass EventRouter.get_recipients with a deterministic list.

    ``recipients=None`` defaults to ``[101]`` so most tests don't have
    to repeat the literal; the empty-recipients short-circuit test
    passes an explicit ``[]`` which MUST NOT trigger the default
    (an earlier draft used ``recipients or [101]`` which incorrectly
    swallowed empty lists -- empty-list callers must keep their
    intent visible).
    """
    if recipients is None:
        recipients = [101]
    em._router.get_recipients = MagicMock(return_value=list(recipients))


def _make_event(event_type=EventType.PRESENCE_UPDATE):
    """Build a minimal Event that's safe for the dispatch hot path."""
    return Event(event_type=event_type, data={"k": "v"})


# --- subscribe() pool shaping --------------------------------------------


def test_subscribe_critical_populates_critical_pool(events_module):
    em = _make_em(events_module)
    cb = MagicMock()
    em.subscribe(cb, critical=True)
    assert cb in em._critical_subscribers
    assert cb not in em._subscribers
    assert events_module._manager._critical_subscribers is em._critical_subscribers


def test_subscribe_default_populates_regular_pool(events_module):
    em = _make_em(events_module)
    cb = MagicMock()
    em.subscribe(cb)
    assert cb in em._subscribers
    assert cb not in em._critical_subscribers


def test_subscribe_dedup_within_single_pool(events_module):
    em = _make_em(events_module)
    cb = MagicMock()
    em.subscribe(cb, critical=True)
    em.subscribe(cb, critical=True)
    assert len(em._critical_subscribers) == 1


def test_subscribe_dedup_across_pools(events_module):
    """Same callback can't be in critical AND regular pool simultaneously."""
    em = _make_em(events_module)
    cb = MagicMock()
    em.subscribe(cb, critical=True)
    em.subscribe(cb, critical=False)  # should be ignored on the regular side
    assert cb in em._critical_subscribers
    assert cb not in em._subscribers


def test_subscribe_merged_cap_eviction_picks_oldest(events_module):
    """A full cap evicts whichever subscriber has the OLDEST _last_used.

    Uses ``monkeypatch.setattr`` to deterministically pin the eviction
    order rather than racing on wall-clock monotonicity (which is
    fragile under high-contention CI runners).
    """
    em = _make_em(events_module)
    em._MAX_SUBSCRIBERS = 2

    fast = MagicMock(name="fast")
    slow = MagicMock(name="slow")
    crit = MagicMock(name="crit")

    em.subscribe(fast, critical=False)
    em.subscribe(slow, critical=False)
    # ``subscribe`` writes ``time.monotonic()`` to _last_used AFTER
    # our pin below, so pin the dict BEFORE the third subscribe
    # triggers the cap-eviction branch -- the cap fires on the third
    # insert, and the branch reads ``self._last_used`` to pick a
    # victim. ``fast`` having the lowest value guarantees its eviction.
    em._last_used = {fast: 0.0, slow: 1.0}
    em.subscribe(crit, critical=True)

    assert fast not in em._subscribers
    assert fast not in em._critical_subscribers
    assert fast not in em._last_used  # cleanup progresses to the dict too
    assert slow in em._subscribers
    assert crit in em._critical_subscribers


def test_unsubscribe_pops_from_both_pools(events_module):
    em = _make_em(events_module)
    cb = MagicMock()
    em.subscribe(cb, critical=True)
    em.unsubscribe(cb)
    assert cb not in em._critical_subscribers
    assert cb not in em._subscribers
    assert cb not in em._last_used


# --- dispatch() contracts ------------------------------------------------


def test_dispatch_empty_recipients_short_circuits(events_module):
    em = _make_em(events_module)
    crit = MagicMock()
    reg = MagicMock()
    em.subscribe(crit, critical=True)
    em.subscribe(reg, critical=False)

    _stub_recipients(em, recipients=[])
    n = em.dispatch(_make_event())

    assert n == 0
    assert not crit.called  # never fan-out if no recipients
    assert not reg.called


def test_dispatch_critical_runs_sync_fanout_branch_before_fallback(events_module):
    """Critical subscribers fire via the SYNC-FANOUT branch, BEFORE the
    RuntimeError fallback in the dispatch body hands the regular pool
    off to ``_fan_out``. Both ultimately run inside the same dispatch
    call (sync tests have no real queue), so this test proves only
    ORDERING under the sync path -- the queue-deferral semantics
    (regular subscribers held back until ``drain``) are covered by
    ``test_dispatch_critical_delivered_even_on_queue_full``.
    """
    em = _make_em(events_module)
    order = []

    def crit_cb(_evt, _rcpts):
        order.append("critical")

    def reg_cb(_evt, _rcpts):
        order.append("regular")

    em.subscribe(crit_cb, critical=True)
    em.subscribe(reg_cb, critical=False)
    _stub_recipients(em, recipients=[42])

    em.dispatch(_make_event())

    assert order == ["critical", "regular"]


def test_dispatch_critical_exception_swallowed(events_module):
    """Per contract (3): exceptions inside critical callbacks are logged
    at ERROR level and DID NOT propagate up to the dispatcher caller.
    """
    em = _make_em(events_module)

    def boom(_evt, _rcpts):
        raise ValueError("simulated critical-cb failure")

    em.subscribe(boom, critical=True)
    _stub_recipients(em, recipients=[7])

    # MUST NOT raise -- contract (3) says raises are swallowed.
    n = em.dispatch(_make_event())
    assert n == 1  # still reports recipients served; drop metrics are
    # covered by the QueueFull test below.


def test_dispatch_critical_delivered_even_on_queue_full(events_module):
    """Per the contract: ``critical=True`` callbacks MUST be delivered
    even when the async queue is saturated.

    Pre-size the queue to 1, drive two async dispatches back-to-back
    inside ``asyncio.run`` so a real loop is active (``get_running_loop``
    resolves), and verify the second dispatch raises ``QueueFull``
    internally -- which means dropped_events escalated BUT the critical
    callback still fired its sync-fanout before the enqueue attempt.
    """
    em = _make_em(events_module)
    em._DISPATCH_QUEUE_MAX = 1

    crit = MagicMock(name="critical")
    reg = MagicMock(name="regular")
    em.subscribe(crit, critical=True)
    em.subscribe(reg, critical=False)
    _stub_recipients(em, recipients=[101])

    async def drive():
        # First dispatch creates the queue (maxsize=1) + enqueues.
        em.dispatch(_make_event())
        # Second dispatch hits QueueFull; critical sync-fanout still runs.
        em.dispatch(_make_event())

    asyncio.run(drive())

    # Critical fires on BOTH -- the queue-full path is lossy ONLY for
    # the regular async subscribers, never for the critical sync lane.
    assert crit.call_count == 2
    metrics = em.get_loss_metrics()
    assert metrics["dropped_events"] == 1
    assert metrics["queue"]["max"] == 1


# --- is_queue_recreate_pending() audit surface --------------------------


def test_is_queue_recreate_pending_no_arg_initially_true(events_module):
    """No queue ever created -> the public audit must say True."""
    em = _make_em(events_module)
    # Fresh instance state -- queue and queue_loop are both None.
    assert em._queue is None
    assert em._queue_loop is None
    assert em.is_queue_recreate_pending() is True


def test_is_queue_recreate_pending_no_arg_false_when_initialised(events_module):
    em = _make_em(events_module)
    em._queue = asyncio.Queue(maxsize=8)
    em._queue_loop = MagicMock(is_closed=lambda: False)
    assert em.is_queue_recreate_pending() is False


def test_is_queue_recreate_pending_no_arg_true_when_captured_loop_closed(events_module):
    em = _make_em(events_module)
    em._queue = asyncio.Queue(maxsize=8)
    em._queue_loop = MagicMock(is_closed=lambda: True)
    assert em.is_queue_recreate_pending() is True


# --- get_loss_metrics() shape --------------------------------------------


def test_get_loss_metrics_initial_state_marks_queue_as_none(events_module):
    """Pre-warm state: queue not yet allocated -- operators can
    distinguish 'not initialised' from 'initialised but empty'."""
    em = _make_em(events_module)
    metrics = em.get_loss_metrics()
    assert metrics["dropped_events"] == 0
    assert metrics["subscribers"] == 0
    assert metrics["critical_subscribers"] == 0
    assert metrics["queue"] is None


def test_get_loss_metrics_after_fake_init_reports_size_and_max(events_module):
    """``get_loss_metrics`` reads ``size`` from ``_queue.qsize()`` and
    ``max`` from ``self._DISPATCH_QUEUE_MAX`` -- two DIFFERENT sources.
    We pin BOTH to 10 so the contract (the queue size reported on the
    metrics endpoint matches the configured ceiling) is asserted on
    actual-production-shape state.
    """
    em = _make_em(events_module)
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    q.put_nowait(("placeholder", [], []))  # 1 item, max 10
    em._queue = q
    # ``get_loss_metrics`` reads ``max`` from the instance attribute
    # (or class fallback); pin it so the test exercises the true
    # production shape -- both the queue itself and the configured
    # ceiling must agree.
    em._DISPATCH_QUEUE_MAX = 10
    em._queue_loop = MagicMock(is_closed=lambda: False)

    metrics = em.get_loss_metrics()
    assert metrics["queue"] == {"size": 1, "max": 10}


def test_get_loss_metrics_reports_critical_count_separately(events_module):
    em = _make_em(events_module)
    em.subscribe(MagicMock(), critical=True)
    em.subscribe(MagicMock(), critical=True)
    em.subscribe(MagicMock(), critical=False)
    metrics = em.get_loss_metrics()
    assert metrics["critical_subscribers"] == 2
    assert metrics["subscribers"] == 1
