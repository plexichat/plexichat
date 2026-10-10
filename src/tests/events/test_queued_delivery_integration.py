"""End-to-end coverage for the queued (async-queue) delivery path.

Production wires the event queue through the app lifespan: ``dispatch``
enqueues onto a loop-bound :class:`asyncio.Queue`, a background consumer
fans events out to regular subscribers, and ``stop_consumer`` drains any
remainder on shutdown. Until now NO test exercised that path end-to-end
(every sync-context dispatch took the eager-fan-out fallback), so a
regression that silently dropped every queued event could not fail CI.

These tests run inside a real event loop and drive the exact sequence
the lifespan uses: subscribe -> start_consumer -> dispatch ->
consumer/drain delivery.
"""

import asyncio

import pytest

from src.core import events
from src.core.events.payloads import create_message_create

pytestmark = [pytest.mark.asyncio]


def _message_event(content, server_id=4433555):
    return create_message_create(
        message_id=99000,
        channel_id=99001,
        author_id=99002,
        content=content,
        server_id=server_id,
    )


async def test_dispatch_queues_then_consumer_delivers_to_regular_subscriber():
    """dispatch() under a running loop must enqueue; the started consumer
    must deliver to regular subscribers exactly like the app lifespan."""
    received = []

    def subscriber(event, user_ids):
        received.append((event, [str(u) for u in user_ids]))

    events.subscribe(subscriber)
    try:
        await events.start_consumer()

        delivered = events.dispatch(
            _message_event("queued hello"), user_ids=["1001", "1002"]
        )
        assert delivered == 2, "dispatch must report full recipient fan-out"
        assert received == [], "delivery must be deferred to the consumer"

        # The consumer polls every _CONSUMER_POLL_INTERVAL_SEC (0.05s).
        await asyncio.sleep(0.15)
        assert len(received) == 1
        event, user_ids = received[0]
        assert event.content == "queued hello"
        assert sorted(user_ids) == ["1001", "1002"]
    finally:
        # Lifespan shutdown path: stops the consumer and drains leftovers.
        await events.stop_consumer()
        events.unsubscribe(subscriber)

    assert len(received) == 1, "shutdown drain must not duplicate delivery"


async def test_drain_flushes_events_stuck_in_queue_without_a_consumer():
    """A loop-bound dispatch with NO running consumer must still deliver
    once drain() runs (the graceful-shutdown guarantee)."""
    received = []

    def subscriber(event, user_ids):
        received.append((event.content, [str(u) for u in user_ids]))

    events.subscribe(subscriber)
    try:
        assert events.dispatch(_message_event("drain me"), user_ids=["2001"]) == 1
        assert received == [], "without a consumer the event waits in the queue"

        manager = events._get_manager()
        assert manager._queue is not None, "queue must exist after an async dispatch"
        await manager.drain()

        assert received == [("drain me", ["2001"])]
        assert manager._queue.empty(), "drain must empty the queue"
    finally:
        events.unsubscribe(subscriber)


async def test_server_scoped_dispatch_routes_recipients_through_wired_modules(
    auth_manager, server_manager
):
    """With production-equivalent routing modules wired, a server-scoped
    dispatch resolves its recipients through the real ServerManager."""
    import uuid

    owner = auth_manager.register(
        username=f"evt_owner_{uuid.uuid4().hex[:8]}",
        email=f"evt_owner_{uuid.uuid4().hex[:8]}@test.local",
        password="TestPass123!",
    )
    member = auth_manager.register(
        username=f"evt_member_{uuid.uuid4().hex[:8]}",
        email=f"evt_member_{uuid.uuid4().hex[:8]}@test.local",
        password="TestPass123!",
    )
    server = server_manager.create_server(owner.id, "Events Routing Server")
    server_manager.add_member(server.id, member.id)

    received = []

    def subscriber(event, users):
        received.append([str(u) for u in users])

    events.subscribe(subscriber)
    try:
        delivered = events.dispatch(
            _message_event("routed", server_id=server.id),
            server_id=server.id,
        )
        assert delivered == 2
        await events._get_manager().drain()
        assert len(received) == 1
        assert sorted(received[0]) == sorted({str(owner.id), str(member.id)})
    finally:
        events.unsubscribe(subscriber)
