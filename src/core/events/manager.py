"""
Event manager - Core event dispatch coordination.

Defers synchronous subscriber fan-out via an ``asyncio.Queue`` so the
request handler that emitted the event is not blocked by slow
subscribers; subscriber list is bounded and TTL-evicted.
"""

import asyncio
import threading
import time
from typing import Optional, List, Callable

import utils.logger as logger
from src.core.base import SnowflakeID

from .models import Event
from .router import EventRouter


class EventManager:
    """Manages event dispatch and subscriptions."""

    # Bounded knobs (constant) — sized for a single Plexichat
    # instance; raise if a deployment needs more headroom.
    _MAX_SUBSCRIBERS = 1024
    _SUBSCRIBER_IDLE_TTL_SEC = 300  # 5 minutes
    _DISPATCH_QUEUE_MAX = 4096

    def __init__(
        self,
        relationships_module=None,
        servers_module=None,
        messaging_module=None,
    ):
        self._router = EventRouter(
            relationships_module=relationships_module,
            servers_module=servers_module,
            messaging_module=messaging_module,
        )
        self._subscribers: dict = {}
        self._critical_subscribers: dict = {}
        self._last_used: dict = {}
        self._lock = threading.Lock()
        # async queue for deferred fan-out; lazy-created so non-async
        # unit tests can still synthesise EventManager without a loop.
        self._queue: Optional[asyncio.Queue] = None
        # Loop the queue was created against — tracked explicitly so
        # we don't need to call the private ``asyncio.Queue._get_loop``
        # API to detect a stale-loop case (uvicorn reload / lifespan
        # reset). Mirrored under ``self._lock`` for atomic swap.
        self._queue_loop: Optional[asyncio.AbstractEventLoop] = None
        # Last prune tick guard so we don't sweep every call.
        self._last_prune = time.monotonic()
        # Observable loss counter: events dropped when the dispatch
        # queue is full. Reading exposes saturation to dashboards.
        # Lock contract: this counter shares ``self._lock`` with the
        # subscriber dict so reads stay consistent with concurrent
        # ``dispatch`` increments from FastAPI's sync threadpool.
        self._dropped_events = 0

        logger.info("Events module initialized")

    # === Subscriber lifecycle ===

    def subscribe(
        self,
        callback: Callable[[Event, List[SnowflakeID]], None],
        *,
        critical: bool = False,
    ) -> None:
        """Register an event subscriber.

        ``critical=True`` opts a callback into the non-lossy delivery
        path: tagged subscribers are invoked synchronously *before*
        the regular async queue, so saturated queues do not drop their
        events. The trade-off for that guarantee is documented
        per-callback intent (e.g. auth-audit, billing-glue, ws-liveness).

        ``critical=False`` (default) keeps backward compatibility —
        every prior caller continues to work unchanged.

        CRITICAL-CALLBACK CONTRACT -- ``critical=True`` subscribers
        agree to ALL of the following. The dispatcher enforces the
        non-lossy side; the subscriber enforces the rest. Skipping
        any one of these is a bug in the subscriber, not the
        dispatcher.

        1. Fast & non-blocking -- slow critical callbacks block the
           dispatching thread. Every direct emit path in the
           request-handler threadpool is affected, so a 50 ms
           critical callback × N concurrent requests steals worker
           capacity proportionally.

        2. Non-reentrant w.r.t. ``dispatch`` -- ``threading.Lock``
           is NOT reentrant. Critical callbacks that synchronously
           call ``self.dispatch(event, ...)`` will DEADLOCK the
           dispatcher. Edge-trigger handlers that emit downstream
           events must schedule the inner dispatch via
           ``asyncio.call_soon`` / ``loop.run_in_executor`` / a
           thread worker instead of running inline. (See rule 5.)

        3. Exception semantics -- ``try/except Exception`` in
           ``dispatch``'s sync-fanout loop SWALLOWS raises inside
           critical callbacks and only logs them at ERROR level.
           Subscribers MUST NOT rely on dispatcher behaviour for
           control flow -- raising inside the callback is logged-
           and-suppressed, never propagated. If you need to fail
           something externally, do it from inside your own
           try/except; the dispatcher will not raise for you.

        4. Read-only inputs -- ``cb(event, recipients)`` shares the
           same ``Event`` object + ``List[SnowflakeID]`` reference
           with every subsequent critical callback iterated in the
           same dispatch. Mutation by one critical cb leaks to the
           rest. Subscribers MUST treat both as read-only; copy
           ``list(recipients)`` or shallow-copy mutable event
           fields before mutating.

        5. ``asyncio`` re-entry is SAFE -- ``asyncio.get_event_loop()
           .call_soon(...)`` from inside a critical callback only
           acquires ``self._lock`` AFTER the outer ``dispatch`` has
           released it (since the outer lock is released BEFORE the
           sync-fanout loop runs). So deferred re-entry via
           ``asyncio.call_soon`` / ``run_in_executor`` / thread
           worker does NOT deadlock. The ONLY unsafe re-entry
           path is a synchronous, in-line call to
           ``self.dispatch(event, ...)`` (rule 2).

        6. Insertion-order preserved --
           ``list(self._critical_subscribers.keys())`` iterates in
           subscription order. First-subscribed fires first. If you
           depend on ordering (e.g. auth-audit BEFORE billing-glue),
           subscribe in that order; future refactors must not
           collapse the two pools or reorder iteration.
        """
        with self._lock:
            self._maybe_prune_locked()
            if callback in self._subscribers or callback in self._critical_subscribers:
                return
            total = len(self._subscribers) + len(self._critical_subscribers)
            if total >= self._MAX_SUBSCRIBERS:
                # Drop oldest idle subscriber across BOTH pools so a
                # runaway producer can't leak the subscriber list
                # indefinitely. Pool-tracking is merged for the cap
                # so critical subscribers don't starve regular ones.
                victim = min(self._last_used, key=lambda k: self._last_used[k])
                self._subscribers.pop(victim, None)
                self._critical_subscribers.pop(victim, None)
                self._last_used.pop(victim, None)
                logger.warning(
                    f"Subscriber list at cap "
                    f"({self._MAX_SUBSCRIBERS}); evicted {victim}"
                )
            target = self._critical_subscribers if critical else self._subscribers
            target[callback] = True
            self._last_used[callback] = time.monotonic()

    def unsubscribe(self, callback: Callable[[Event, List[SnowflakeID]], None]) -> None:
        with self._lock:
            # Pop from BOTH pools — callback may have been subscribed
            # critical and then dropped the flag in a later call.
            self._subscribers.pop(callback, None)
            self._critical_subscribers.pop(callback, None)
            self._last_used.pop(callback, None)

    def _needs_queue_recreate_locked(self, loop) -> bool:
        """Return True if the dispatch queue must be (re)created.

        The queue is lazy-allocated against the first loop that
        calls ``dispatch``. The recreate predicate fires on four
        distinct conditions so the invariant is centralised in one
        place rather than spread across the dispatch hot path:

        1. never created yet (``_queue is None``)
        2. loop reference missing (``_queue_loop is None``) — keep
           this in lock-step with ``_queue`` so a future refactor
           that lazy-creates one without the other doesn't silently
           raise ``AttributeError``
        3. FastAPI lifespan reset / uvicorn reload superseded the
           captured loop (``_queue_loop is not loop``)
        4. captured loop has been closed but not yet replaced
           (``_queue_loop.is_closed()``) — ``is_closed`` is the
           public-API signal so we don't hold onto a dangling
           strong reference in long-running deployments.

        LOCK CONTRACT: the caller MUST hold ``self._lock``; the helper
        follows the ``_maybe_prune_locked`` naming convention to make
        that requirement explicit. Without the lock the read of
        ``_queue`` / ``_queue_loop`` could race against a concurrent
        ``dispatch`` from another thread performing its own recreate.
        """
        return (
            self._queue is None
            or self._queue_loop is None
            or self._queue_loop is not loop
            or self._queue_loop.is_closed()
        )

    def _maybe_prune_locked(self) -> None:
        """Prune subscribers that haven't been touched in TTL_SEC.

        Caller MUST hold ``self._lock`` so the prune is consistent
        with concurrent subscribe / unsubscribe.
        """
        now = time.monotonic()
        if now - self._last_prune < max(1, self._SUBSCRIBER_IDLE_TTL_SEC // 4):
            return
        cutoff = now - self._SUBSCRIBER_IDLE_TTL_SEC
        stale = [cb for cb, ts in list(self._last_used.items()) if ts < cutoff]
        for cb in stale:
            self._subscribers.pop(cb, None)
            self._last_used.pop(cb, None)
        if stale:
            logger.debug(f"Evicted {len(stale)} idle event subscribers")
        self._last_prune = now

    # === Dispatch ===

    def dispatch(
        self,
        event: Event,
        user_ids: Optional[List[SnowflakeID]] = None,
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        exclude_user_ids: Optional[List[SnowflakeID]] = None,
    ) -> int:
        # Get recipients synchronously — the routing query is bounded
        # and quick.
        recipients = self._router.get_recipients(
            event=event,
            user_ids=user_ids,
            server_id=server_id,
            channel_id=channel_id,
            exclude_user_ids=exclude_user_ids,
        )

        if not recipients:
            return 0

        # Capture the current subscriber snapshot under the lock so
        # we don't race against subscribe/unsubscribe mid-fan-out.
        # Critical subscribers are snapshotted separately so the
        # QueueFull handler can sync-invoke them (non-lossy delivery
        # for tagged subs) WITHOUT forcing the regular async queue
        # path to also process them.
        with self._lock:
            self._maybe_prune_locked()
            critical_snapshot = list(self._critical_subscribers.keys())
            subscribers = [(cb, self._last_used[cb]) for cb in self._subscribers]
            for cb, _ in subscribers:
                self._last_used[cb] = time.monotonic()
            for cb in critical_snapshot:
                self._last_used[cb] = time.monotonic()

        # Non-lossy delivery for ``critical=True`` subscribers lives here.
        # Full contract on ``subscribe()``; this block enforces only the
        # dispatcher side (sync-call BEFORE queue.put_nowait).
        if critical_snapshot:
            for cb in critical_snapshot:
                try:
                    cb(event, recipients)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Critical event subscriber error: {exc}")

        # BACKPRESSURE: enqueue onto the async queue instead of
        # synchronously iterating subscribers. If the queue is full,
        # we DROP the event (lossy) rather than blocking the request
        # handler that emitted it. The drop counter is exposed so
        # operators can detect saturation via metrics.
        #
        # Python 3.10+ deprecates ``asyncio.get_event_loop()`` /
        # ``get_event_loop_policy().get_event_loop()`` when called
        # outside a running loop, so we ONLY consult the running loop
        # here. If no loop is running we fall through the top-level
        # ``except RuntimeError`` to the eager-fanout branch (which
        # also covers Celery / sync CLI paths).
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # Defensive stale-loop check: ``self._queue`` is
                # lazy-created against the FIRST loop that called us.
                # If the FastAPI app has since restarted its loop
                # (uvicorn reload, lifespan reset), OR if the loop we
                # captured has been closed without a swap-in yet,
                # ``put_nowait`` would target a closed-loop queue.
                # ``loop.is_closed()`` is the public-API signal for
                # "no longer running" — checking it here lets us drop
                # a stale strong ref to a closed loop and free the
                # memory in long-running deployments. The swap is
                # performed under ``_lock`` so a concurrent
                # ``dispatch`` from another thread can't observe a
                # half-recreated queue mid-flight.
                with self._lock:
                    if self._needs_queue_recreate_locked(loop):
                        self._queue = asyncio.Queue(maxsize=self._DISPATCH_QUEUE_MAX)
                        self._queue_loop = loop
                assert self._queue is not None  # narrowed under ``self._lock`` above
                try:
                    self._queue.put_nowait((event, recipients, subscribers))
                except asyncio.QueueFull:
                    # ``dispatch`` can run from FastAPI's sync
                    # threadpool, so the counter must be locked —
                    # ``+=`` is non-atomic read-modify-write.
                    # NOTE: critical subscribers have ALREADY been
                    # sync-invoked above (non-lossy delivery path).
                    # Increment the drop counter ONLY for the regular
                    # subscribers that the saturated queue couldn't
                    # accept.
                    with self._lock:
                        self._dropped_events += 1
                        dropped = self._dropped_events
                    logger.error(
                        "Event dispatch queue full, served "
                        f"{len(critical_snapshot)} critical; dropping "
                        f"{event.event_type} for {len(recipients)} users "
                        f"(total drops: {dropped})"
                    )
                    return len(
                        recipients
                    )  # critical already fan-out-ed; queue drop ends dispatch
                return len(
                    recipients
                )  # sync CTX: regular subscribers will drain via lifespan
        except RuntimeError:
            # No running loop in this thread (Celery worker / sync
            # CLI / unit test scaffolding); eager fan-out.
            pass
        self._fan_out(event, recipients, subscribers)

        logger.debug(f"Dispatched {event.event_type.value} to {len(recipients)} users")
        return len(recipients)

    def _fan_out(
        self,
        event: Event,
        recipients: List[SnowflakeID],
        subscribers: list,
    ) -> None:
        for callback, _ts in subscribers:
            try:
                callback(event, recipients)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Event subscriber error: {exc}")

    async def drain(self) -> None:
        """Drain the queue once by fanning out each enqueued event.

        Call from an async entry point (FastAPI lifespan / startup
        task) to keep the dispatcher worker-friendly.
        """
        if self._queue is None:
            return
        while not self._queue.empty():
            event, recipients, subscribers = await self._queue.get()
            self._fan_out(event, recipients, subscribers)

    def get_loss_metrics(self) -> dict:
        """Expose dispatch saturation metrics for observability.

        Returns a dict with the cumulative dropped-event count, the
        current subscriber count, and (when the async queue has been
        initialised) the current ``qsize`` and ``maxsize``. Callers
        (e.g. /api/v1/health) can expose these so operators can
        detect backpressure.
        """
        with self._lock:
            critical_count = len(self._critical_subscribers)
            if self._queue is None or self._queue_loop is None:
                # Pre-warm: dispatcher hasn't enqueued an async event
                # yet. Surface this distinctly so dashboards can tell
                # "uninitialised" apart from "initialised but empty".
                return {
                    "dropped_events": self._dropped_events,
                    "subscribers": len(self._subscribers),
                    "critical_subscribers": critical_count,
                    "queue": None,
                }
            return {
                "dropped_events": self._dropped_events,
                "subscribers": len(self._subscribers),
                "critical_subscribers": critical_count,
                "queue": {
                    "size": self._queue.qsize(),
                    "max": self._DISPATCH_QUEUE_MAX,
                },
            }

    def is_queue_recreate_pending(self, loop=None) -> bool:
        """Public audit surface — would the next dispatch recreate the queue?

        Used by :func:`src.api.routes.health` so operators can detect
        loop resets, uvicorn reloads, and stale-loop drift before
        dispatching a real event causes a queue migration. Safe to
        call outside a running loop: when ``loop`` is omitted the
        helper checks the conditions that don't require a current-
        loop identity comparison (None queue, None loop ref, closed
        captured loop). When ``loop`` is provided the caller's
        public-API loop is used for the identity-mismatch branch via
        :meth:`_needs_queue_recreate_locked`.

        Returns ``True`` if the next ``dispatch`` will trigger queue
        (re)creation, ``False`` otherwise.
        """
        if loop is None:
            # Audit path: simulate the recreate predicate without
            # requiring a running loop, mirroring conditions 1, 2, 4.
            # Condition 3 (identity mismatch) only fires when
            # compared against a *current* loop, which we don't have
            # here — it is intentionally excluded since the operator
            # can't ask "next dispatch against which loop?" without
            # a running context.
            with self._lock:
                if self._queue is None or self._queue_loop is None:
                    return True
                captured = self._queue_loop
            return captured.is_closed()
        # Caller has a running loop; defer to the locked helper.
        with self._lock:
            return self._needs_queue_recreate_locked(loop)
