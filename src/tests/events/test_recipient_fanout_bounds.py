"""Regression: recipient resolution must be deduped and hard-bounded.

``EventRouter.get_recipients`` feeds every delivery lane: critical subscribers
(invoked synchronously on the emitting worker), the bounded async queue (which
stores the list by value), and the cross-worker Valkey pub/sub payload (which
serialises the whole list and every sibling worker re-fans out). Before this
fix an explicit ``user_ids`` audience was returned unbounded and with
duplicates intact, so one caller (or one oversized server) could amplify a
single event into unbounded work/memory on every worker and deliver the same
event repeatedly.

These tests lock in the two invariants that close that gap: dedupe and a hard
ceiling, on both the explicit and the resolved-audience paths.
"""

from unittest.mock import MagicMock

import pytest

from src.core.events.manager import EventManager
from src.core.events.models import Event
from src.core.events.router import EventRouter
from src.core.events.types import EventType


pytestmark = pytest.mark.unit


# --- dedupe -----------------------------------------------------------------


def test_explicit_user_ids_are_deduped_preserving_first_seen_order():
    """Repeated ids in a caller-supplied audience collapse to one delivery."""
    router = EventRouter()
    assert router.get_recipients(None, user_ids=[7, 3, 7, 3, 9, 7]) == [7, 3, 9]


# --- hard bound -------------------------------------------------------------


def test_explicit_user_ids_are_hard_capped_and_observable():
    """A single caller cannot enqueue an unbounded recipient list."""
    router = EventRouter()
    router.MAX_RECIPIENTS = 5

    result = router.get_recipients(None, user_ids=list(range(100)))

    assert result == [0, 1, 2, 3, 4]
    assert router.get_routing_metrics() == {
        "max_recipients": 5,
        "truncated_recipient_fanouts": 1,
    }


def test_generator_input_is_stopped_early_not_just_stored_bounded():
    """Bounded work, not just bounded memory: we stop reading at the cap."""
    router = EventRouter()
    router.MAX_RECIPIENTS = 10
    yielded = []

    def huge_stream():
        for i in range(1_000_000):
            yielded.append(i)
            yield i

    result = router.get_recipients(None, user_ids=huge_stream())

    assert result == list(range(10))
    # Stopped within one unique id of the ceiling instead of draining a
    # million-element stream.
    assert len(yielded) == 11


def test_resolved_server_members_are_capped_too():
    """The set-based routing path has the same ceiling as explicit ids."""
    servers = MagicMock()
    servers.get_member_user_ids.return_value = list(range(50))
    router = EventRouter(servers_module=servers)
    router.MAX_RECIPIENTS = 8

    event = Event(event_type=EventType.MESSAGE_CREATE, data={}, server_id=900)
    result = router.get_recipients(event)

    assert result == list(range(8))
    assert router.get_routing_metrics()["truncated_recipient_fanouts"] == 1


# --- no regression in normal behaviour -------------------------------------


def test_exclusions_still_apply_before_bounding():
    """The fix must not weaken the existing exclusion filter."""
    router = EventRouter()
    router.MAX_RECIPIENTS = 3
    result = router.get_recipients(
        None, user_ids=[1, 2, 3, 4, 5], exclude_user_ids=[2, 4]
    )
    assert result == [1, 3, 5]


def test_dispatch_bounds_recipients_across_all_lanes():
    """The ceiling reaches critical subscribers AND the cross-worker payload."""
    em = EventManager()
    seen = {}

    def critical(_event, recipients):
        seen["recipients"] = list(recipients)

    em.subscribe(critical, critical=True)
    em._publish_cross_worker = MagicMock()

    # Over the default cap, with duplicates mixed in.
    audience = list(range(EventRouter.MAX_RECIPIENTS + 50)) + [0, 1, 2]
    returned = em.dispatch(
        Event(event_type=EventType.READY, data={}), user_ids=audience
    )

    cap = EventRouter.MAX_RECIPIENTS
    assert returned == cap
    assert seen["recipients"] == list(range(cap))
    # Recipients handed to the cross-worker publisher are deduped + bounded,
    # so the published Valkey payload and every sibling's re-fan-out are too.
    cross_recipients = em._publish_cross_worker.call_args[0][1]
    assert cross_recipients == list(range(cap))
