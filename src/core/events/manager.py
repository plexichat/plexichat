"""
Event manager - Core event dispatch coordination.

Defers synchronous subscriber fan-out via an ``asyncio.Queue`` so the
request handler that emitted the event is not blocked by slow
subscribers; subscriber list is bounded and TTL-evicted.
"""

import asyncio
import json
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Optional, List, Callable, Dict, Tuple

import utils.logger as logger
from src.core.base import SnowflakeID

from .dedupe import cross_worker_dedupe
from .models import Event
from .router import EventRouter

# Valkey pub/sub channel used to forward events to sibling workers.
_CROSS_WORKER_CHANNEL = "events:cross_worker"

# A burst of events to the same audience (typing, presence, reactions) rebuilds
# the *same* cross-worker envelope over and over: the only field that differs
# is the per-publication ``id``. Serialize the envelope once per distinct
# identity and stamp the unique id into the cached template. The marker below
# is a sentinel JSON string, never a real id, and ``id`` is the first key, so
# a single left-to-right replace swaps only the envelope's own id even if the
# payload happens to contain the same bytes.
_ENVELOPE_ID_MARKER = "__plexichat_envelope_id_9c2f41ab__"
_ENVELOPE_ID_JSON = json.dumps(_ENVELOPE_ID_MARKER)
_MAX_ENVELOPE_TEMPLATES = 64


def _freeze_envelope_value(value: Any) -> Any:
    """Hashable, structure-preserving key for a JSON-like value (no re-dumps)."""
    if isinstance(value, dict):
        return (
            "d",
            tuple(sorted((str(k), _freeze_envelope_value(v)) for k, v in value.items())),
        )
    if isinstance(value, (list, tuple)):
        return ("l", tuple(_freeze_envelope_value(v) for v in value))
    if isinstance(value, (set, frozenset)):
        return ("s", tuple(sorted((_freeze_envelope_value(v) for v in value), key=repr)))
    if isinstance(value, (str, int, float, bool, type(None))):
        return ("v", type(value).__name__, value)
    try:
        hash(value)
    except TypeError:
        return ("r", repr(value))
    return ("v", type(value).__name__, value)


class _EnvelopeTemplateCache:
    """Bounded LRU of serialized cross-worker envelopes keyed by identity."""

    def __init__(self, max_entries: int = _MAX_ENVELOPE_TEMPLATES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[Tuple[Any, ...], str]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def get(self, key: Tuple[Any, ...]) -> Optional[str]:
        with self._lock:
            template = self._entries.get(key)
            if template is not None:
                self._hits += 1
                self._entries.move_to_end(key)
                return template
            self._misses += 1
            return None

    def put(self, key: Tuple[Any, ...], template: str) -> None:
        with self._lock:
            self._entries[key] = template
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0


_envelope_templates = _EnvelopeTemplateCache()


def get_envelope_template_cache_stats() -> Dict[str, int]:
    """Expose hit/miss/size counters for observability and regression tests."""
    return _envelope_templates.stats()


def clear_envelope_template_cache() -> None:
    """Invalidate cached templates (intent/worker change, tests)."""
    _envelope_templates.clear()


def _envelope_scope_id(value) -> Optional[int]:
    """Normalize an ``Event`` scope id for the cross-worker envelope.

    ``SnowflakeID`` may be a decimal string on the wire; the receiving worker
    needs a plain int (or ``None``) for the intent router. Booleans and
    unparseable values collapse to ``None`` rather than raising out of the
    publish path.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


class EventManager:
    """Manages event dispatch and subscriptions."""

    # Bounded knobs (constant) — sized for a single Plexichat
    # instance; raise if a deployment needs more headroom.
    _MAX_SUBSCRIBERS = 1024
    # Critical subscribers are immune to BOTH merged-cap eviction and
    # idle-TTL pruning (see ``subscribe`` / ``_maybe_prune_locked``), so the
    # merged cap alone cannot bound that pool: once every slot is critical,
    # each new ``subscribe(..., critical=True)`` grows the dict (and
    # ``_last_used``) forever. Give the non-lossy lane its own hard ceiling.
    # Production registers only a handful (gateway dispatcher, TLS hooks).
    _MAX_CRITICAL_SUBSCRIBERS = 64
    _SUBSCRIBER_IDLE_TTL_SEC = 300  # 5 minutes
    _DISPATCH_QUEUE_MAX = 4096
    # Consumer poll interval (seconds). The queue consumer batches all
    # currently-queued events, then sleeps. Regular subscribers are
    # non-critical extras (the WebSocket dispatcher is a critical,
    # synchronous subscriber), so this latency budget is fine.
    _CONSUMER_POLL_INTERVAL_SEC = 0.05

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
        # Observable idle-eviction counter: subscribers removed by the
        # idle-TTL sweep (B2) were previously only DEBUG-logged, hiding
        # listener loss from operators. Exposed via ``get_loss_metrics``.
        self._evicted_subscribers = 0
        # Cross-worker event publishing: worker_id is used to tag
        # each published event so sibling workers can ignore their
        # own messages (avoids infinite echo loop).
        self._worker_id: str = "unknown"
        # Lazy-loaded Valkey client reference for cross-worker pub/sub.
        # Set via ``set_worker_id`` when the worker identity is known.
        self._cross_worker_drops: int = 0
        # Background queue consumer (A1): without a running consumer the
        # deferred-fan-out queue is write-only and regular subscribers
        # never receive events. Started/stopped from the app lifespan so
        # the consumer shares the server's event loop.
        self._consumer_task: Optional[asyncio.Task] = None
        # Stop flag for the consumer; guarded by ``self._lock`` because
        # ``dispatch`` may run on other threads concurrently.
        self._consumer_stop = False

        logger.info("Events module initialized")

    def set_worker_id(self, worker_id: str) -> None:
        """Tag events published to Valkey with this worker's identity.

        Must be called after the Valkey client has been assigned its
        worker ID (usually during server initialisation). Events
        published before this call will be tagged ``"unknown"`` and
        will still be received by sibling workers (including this
        one — harmless re-delivery, but wasteful).
        """
        self._worker_id = worker_id

    # ------------------------------------------------------------------
    # Cross-worker publish helper
    # ------------------------------------------------------------------

    def _publish_cross_worker(
        self, event: Event, recipients: List[SnowflakeID]
    ) -> None:
        """Publish an event to the cross-worker Valkey channel.

        This is called AFTER local fan-out so local subscribers always
        receive the event before (or at the same time as) remote
        workers.

        Backend state is classified, not probed-then-assumed: "Valkey
        unavailable" is a silent local-only no-op (current
        single-instance behaviour, no drop counted), while an error
        *during* publish bumps ``_cross_worker_drops`` (surfaced via
        :meth:`get_loss_metrics`). A serialization failure is neither —
        it is a deterministic caller bug, so it warns loudly instead of
        blaming the backend.
        """
        try:
            from src.core.database import get_valkey_client, valkey_available
        except Exception as e:
            # No Valkey package / import failure: single-instance mode,
            # the event simply stays local.
            logger.debug(f"Cross-worker publish unavailable (import): {e}")
            return

        try:
            available = valkey_available()
        except Exception as e:
            # The probe itself must never break local delivery.
            logger.debug(f"Cross-worker availability probe failed: {e}")
            return
        if not available:
            return
        client = get_valkey_client()
        if client is None:
            return

        # Give every publication a globally-unique id.  The receiver uses it
        # to collapse duplicates (e.g. our own pre-identity ``sw="unknown"``
        # echo, or a re-delivery after a sibling reconnects).  Recording the
        # id here lets this worker recognise its own echo before the pub/sub
        # round-trip completes.  The shared registry is bounded + TTL'd, so
        # this adds no unbounded "retained message" growth.
        event_id = f"{self._worker_id}:{uuid.uuid4().hex}"
        try:
            # Carry the AUTHORITATIVE routing scope in its own envelope field.
            # ``event.data`` is not required to duplicate ``server_id``/
            # ``channel_id`` (several payload builders omit them), so the old
            # envelope forced the receiver to re-infer scope from ``d``. A
            # guild event whose data lacks ``guild_id`` was then classified as
            # a DM on sibling workers and gated by the DM intent instead of
            # the guild one (and vice versa) -- an intent-gate/audience leak.
            # The receiver prefers this explicit scope and only falls back to
            # data-derived scope for legacy payloads without the field.
            scope_server = _envelope_scope_id(event.server_id)
            scope_channel = _envelope_scope_id(event.channel_id)

            # The complete serialization identity: every value that appears in
            # the envelope *except* the unique publication id. Two events that
            # differ in any of these get different templates, so a cached
            # envelope can never be stamped with the wrong audience or scope.
            cache_key = (
                event.event_type.value,
                self._worker_id,
                scope_server,
                scope_channel,
                _freeze_envelope_value(event.data),
                _freeze_envelope_value(recipients),
            )
            template = _envelope_templates.get(cache_key)
            if template is None:
                template = json.dumps(
                    {
                        "id": _ENVELOPE_ID_MARKER,
                        "t": event.event_type.value,
                        "d": event.data,
                        # SECURITY: recipients reach here via the router,
                        # which dedupes/caps but does not type-check; callers of
                        # events.dispatch(user_ids=...) may hand raw DB ids.
                        # int() laundering would stamp a wrong principal into the
                        # cross-worker wire audience. type() also rejects bool;
                        # genuine integer ids are preserved verbatim.
                        "u": [uid for uid in recipients if type(uid) is int],
                        "sw": self._worker_id,
                        "scope": [scope_server, scope_channel],
                    }
                )
                _envelope_templates.put(cache_key, template)
            # ``id`` is the first key, so the marker's JSON string occurs first
            # in the template; replacing one occurrence swaps only this
            # publication's id and leaves the wire shape byte-identical.
            payload = template.replace(_ENVELOPE_ID_JSON, json.dumps(event_id), 1)
        except (TypeError, ValueError) as e:
            logger.warning(
                "Cross-worker publish skipped for %s: payload not serializable (%s)",
                event.event_type.value,
                e,
            )
            return
        # Remember before publishing so a fast local echo cannot race ahead
        # of the record and be mistaken for a fresh remote event.
        cross_worker_dedupe.remember(event_id)
        try:
            client.publish(_CROSS_WORKER_CHANNEL, payload)
        except Exception as e:
            with self._lock:
                self._cross_worker_drops += 1
                drops = self._cross_worker_drops
            logger.debug(
                "Cross-worker publish failed for %s (drops: %d): %s",
                event.event_type.value,
                drops,
                e,
            )

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

        2. Non-reentrant w.r.t. ``dispatch`` -- a critical callback that
           synchronously calls ``self.dispatch(event, ...)`` does NOT
           deadlock (the dispatcher lock is released BEFORE callbacks
           run), but it recurses WITHOUT BOUND: each nested dispatch
           fans out to every subscriber again, so an accidental
           feedback loop (A emits -> B -> A) exhausts the stack /
           steals the dispatching thread. Edge-trigger handlers that
           emit downstream events must schedule the inner dispatch via
           ``asyncio.call_soon`` / ``loop.run_in_executor`` / a thread
           worker instead of running inline. (See rule 5.)

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
           worker does NOT deadlock. The ONLY hazardous re-entry
           path is a synchronous, in-line call to
           ``self.dispatch(event, ...)`` — not a deadlock (the lock
           is already released) but unbounded recursion (rule 2).

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
            if (
                critical
                and len(self._critical_subscribers) >= self._MAX_CRITICAL_SUBSCRIBERS
            ):
                # Fail closed rather than demote/evict an existing critical
                # callback: silently dropping one would break the non-lossy
                # delivery guarantee the caller opted into. Refuse the new
                # subscription and surface it so operators can see the
                # saturation instead of leaking an unbounded subscriber map.
                logger.warning(
                    f"Critical subscriber cap "
                    f"({self._MAX_CRITICAL_SUBSCRIBERS}) reached; "
                    "refusing new critical subscriber"
                )
                return
            total = len(self._subscribers) + len(self._critical_subscribers)
            if total >= self._MAX_SUBSCRIBERS:
                # Drop the oldest idle REGULAR subscriber so a runaway
                # producer can't leak the subscriber list indefinitely.
                # SECURITY/reliability: critical subscribers are exempt
                # from cap eviction — they carry non-lossy delivery
                # (e.g. real-time gateway fan-out) and evicting one to
                # make room for an ordinary subscription silently
                # breaks that guarantee.
                regular_keys = list(self._subscribers.keys())
                if regular_keys:
                    victim = min(regular_keys, key=lambda k: self._last_used.get(k, 0))
                    self._subscribers.pop(victim, None)
                    self._last_used.pop(victim, None)
                    logger.warning(
                        f"Subscriber list at cap "
                        f"({self._MAX_SUBSCRIBERS}); evicted regular {victim}"
                    )
                # No regular subscriber available to evict: allow the cap
                # to be exceeded rather than dropping a critical (or the
                # incoming) subscriber. TTL pruning still bounds growth.
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
        """Prune idle REGULAR subscribers after TTL_SEC.

        Caller MUST hold ``self._lock`` so the prune is consistent
        with concurrent subscribe / unsubscribe.

        Critical subscribers are deliberately IMMUNE to idle-TTL
        eviction: they are long-lived, non-lossy consumers (e.g. the
        websocket dispatcher) whose absence would silently break
        delivery guarantees. Their lifetime is bounded by explicit
        ``unsubscribe()`` calls, not by idleness.
        """
        now = time.monotonic()
        if now - self._last_prune < max(1, self._SUBSCRIBER_IDLE_TTL_SEC // 4):
            return
        cutoff = now - self._SUBSCRIBER_IDLE_TTL_SEC
        # Only REGULAR subscribers are eligible; critical callbacks must keep
        # their ``_last_used`` entry (B3) so cap eviction's ``min(_last_used)``
        # selection and the merged-pool bookkeeping stay consistent even when
        # a critical subscriber is idle for longer than the TTL.
        stale = [
            cb
            for cb, ts in list(self._last_used.items())
            if ts < cutoff and cb not in self._critical_subscribers
        ]
        for cb in stale:
            self._subscribers.pop(cb, None)
            self._last_used.pop(cb, None)
        if stale:
            self._evicted_subscribers += len(stale)
            logger.warning(
                f"Evicted {len(stale)} idle event subscribers "
                f"(total idle evictions: {self._evicted_subscribers})"
            )
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
                except Exception as exc:
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
                    # Cross-worker publish happens even when the
                    # local async queue is saturated — sibling
                    # workers may still have capacity.
                    self._publish_cross_worker(event, recipients)
                    # ACCURATE RETURN (B1): the recipients were NOT
                    # delivered to by the deferred lane — report 0 so
                    # callers can detect the drop instead of mistaking
                    # it for a successful fan-out. Critical callbacks
                    # did run, but they are an implementation detail,
                    # not recipient delivery.
                    return 0
                # Cross-worker publish after successful async enqueue.
                self._publish_cross_worker(event, recipients)
                # The consumer task started by the app lifespan owns
                # this queue; returning ``len(recipients)`` here is now
                # accurate because queued events are guaranteed to be
                # fanned out (and drained on shutdown).
                logger.debug(
                    f"Dispatched {event.event_type.value} to "
                    f"{len(recipients)} users (queued)"
                )
                return len(recipients)
        except RuntimeError:
            # No running loop in this thread (Celery worker / sync
            # CLI / unit test scaffolding); eager fan-out.
            pass
        self._fan_out(event, recipients, subscribers)

        # Forward to sibling workers via Valkey pub/sub.  Publish
        # happens *after* local fan-out so local clients always
        # receive the event without waiting on the Valkey round-trip.
        self._publish_cross_worker(event, recipients)

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
            except Exception as exc:
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

    async def start_consumer(self) -> None:
        """Start the background queue consumer (idempotent).

        Must be awaited from a running event loop (the app lifespan).
        The consumer owns the dispatch queue for this loop: events
        enqueued by ``dispatch`` are fanned out to regular
        subscribers shortly after enqueue. Without a consumer the
        queue is write-only and regular subscribers starve.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._consumer_task is not None and not self._consumer_task.done():
                return
            # Bind (or re-bind) the queue to THIS loop so ``dispatch``
            # calls running on it find a compatible queue immediately,
            # instead of lazily recreating one on first use.
            if self._needs_queue_recreate_locked(loop):
                self._queue = asyncio.Queue(maxsize=self._DISPATCH_QUEUE_MAX)
                self._queue_loop = loop
            self._consumer_stop = False
            self._consumer_task = loop.create_task(self._consume_loop())
        logger.info("Event dispatch queue consumer started")

    async def _consume_loop(self) -> None:
        """Consume the dispatch queue until stopped.

        Batches all currently-queued events each tick, then sleeps.
        The stop flag is checked under ``self._lock`` so a concurrent
        ``stop_consumer`` is observed promptly; graceful shutdown then
        drains any remainder via :meth:`stop_consumer`.
        """
        while True:
            with self._lock:
                stop_requested = self._consumer_stop
                queue = self._queue
            if stop_requested:
                break
            if queue is None:
                await asyncio.sleep(self._CONSUMER_POLL_INTERVAL_SEC)
                continue
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                event, recipients, subscribers = item
                self._fan_out(event, recipients, subscribers)
            await asyncio.sleep(self._CONSUMER_POLL_INTERVAL_SEC)

    async def stop_consumer(self) -> None:
        """Stop the background consumer and drain the remaining queue.

        Safe to call when no consumer was started. After signalling
        the loop to exit and awaiting its completion, any events still
        sitting in the queue are fanned out synchronously so shutdown
        does not silently drop queued events.
        """
        with self._lock:
            task = self._consumer_task
            self._consumer_task = None
            self._consumer_stop = True
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.drain()
        logger.info("Event dispatch queue consumer stopped")

    def get_loss_metrics(self) -> dict:
        """Expose dispatch saturation metrics for observability.

        Returns a dict with the cumulative dropped-event count, the
        current subscriber count, the cumulative cross-worker publish
        drops, and (when the async queue has been initialised) the
        current ``qsize`` and ``maxsize``. Callers (e.g. /api/v1/health)
        can expose these so operators can detect backpressure.
        """
        with self._lock:
            critical_count = len(self._critical_subscribers)
            if self._queue is None or self._queue_loop is None:
                # Pre-warm: dispatcher hasn't enqueued an async event
                # yet. Surface this distinctly so dashboards can tell
                # "uninitialised" apart from "initialised but empty".
                return {
                    "dropped_events": self._dropped_events,
                    "evicted_subscribers": self._evicted_subscribers,
                    "cross_worker_drops": self._cross_worker_drops,
                    "subscribers": len(self._subscribers),
                    "critical_subscribers": critical_count,
                    "queue": None,
                }
            return {
                "dropped_events": self._dropped_events,
                "evicted_subscribers": self._evicted_subscribers,
                "cross_worker_drops": self._cross_worker_drops,
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
