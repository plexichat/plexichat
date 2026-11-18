"""Bounded background job dispatch for notification fan-out and push delivery.

A single long-lived consumer thread drains a bounded ``queue.Queue`` of jobs.
Jobs are dropped (never block the caller) when the queue is full — notification
work is strictly best-effort and must never back-pressure the message-send
path.

Scaling hardening:

* **Bounded queue** — the queue stays fixed-size; overload sheds work instead
  of growing memory without limit.
* **De-duplication** — callers may attach a ``key`` to a job. While a job with
  the same key is pending (queued or running), later submissions are dropped,
  so a burst of retries for the same message/notification cannot occupy
  multiple slots or repeat the same fan-out.
* **Per-job timeout and back-pressure** — each job runs on a short-lived daemon
  thread and the consumer waits at most ``timeout`` seconds for it. A hung job
  can therefore no longer stall the single consumer and pile up the queue.
  The number of concurrently live job threads is capped by ``max_active_jobs``;
  further jobs are shed while that cap is reached.

Shutdown: every dispatcher created through :func:`get_notification_dispatcher`
is registered once with ``atexit`` so pending jobs are drained and the
consumer thread is joined on interpreter exit.
"""

import atexit
import os
import queue
import threading
from typing import Any, Callable, Optional, Set

import utils.logger as logger

DEFAULT_QUEUE_BOUND = 1000
DEFAULT_JOB_TIMEOUT = 30.0
MAX_ACTIVE_JOBS = 4
SHUTDOWN_DRAIN_TIMEOUT = 5.0

SYNC_DISPATCH_ENV = "PLEXICHAT_SYNC_NOTIFICATION_DISPATCH"


def is_sync_notification_dispatch() -> bool:
    """True when notification dispatch should run inline (tests/embedded)."""
    return os.environ.get(SYNC_DISPATCH_ENV, "") not in ("", "0")


class BoundedBackgroundDispatcher:
    """Single-consumer worker draining a bounded queue of callables.

    ``queue_bound`` caps pending work, ``job_timeout`` is the default deadline
    for a single job, and ``max_active_jobs`` caps how many job threads may be
    alive at once (the back-pressure ceiling).
    """

    def __init__(
        self,
        name: str,
        queue_bound: int = DEFAULT_QUEUE_BOUND,
        job_timeout: float = DEFAULT_JOB_TIMEOUT,
        max_active_jobs: int = MAX_ACTIVE_JOBS,
    ):
        self._name = name
        self._queue: "queue.Queue" = queue.Queue(maxsize=max(1, int(queue_bound)))
        # RLock: submit() holds it while calling _ensure_worker().
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._thread_stop: Optional[threading.Event] = None
        self._job_timeout = max(0.0, float(job_timeout))
        self._max_active_jobs = max(1, int(max_active_jobs))
        self._pending_keys: Set[Any] = set()
        self._active_jobs = 0

    def submit(
        self,
        job: Callable[[], Any],
        *,
        key: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        """Enqueue ``job`` for the consumer thread.

        Returns False (the job is shed) when the queue is full, when ``key`` is
        still pending, or when the active-job ceiling is hit at execution time.
        ``timeout`` overrides this dispatcher's default per-job deadline.
        """
        if job is None:
            return False
        if key is not None:
            try:
                hash(key)
            except TypeError:
                # Unhashable keys still dedupe, just by their repr.
                key = repr(key)
        with self._lock:
            if key is not None and key in self._pending_keys:
                return False
            try:
                self._queue.put_nowait((job, key, timeout))
            except queue.Full:
                return False
            if key is not None:
                self._pending_keys.add(key)
            self._ensure_worker()
        return True

    def pending(self) -> int:
        return self._queue.qsize()

    def active_jobs(self) -> int:
        """Number of job threads currently alive (test/observability helper)."""
        with self._lock:
            return self._active_jobs

    def drain(self, timeout: float = SHUTDOWN_DRAIN_TIMEOUT) -> None:
        """Flush pending jobs and join the worker. Safe to call repeatedly.

        A fresh stop token is installed so a dispatcher can be reused after a
        drain; an in-flight consumer keeps its own (now set) token and exits
        once the queue is empty.
        """
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            self._stop_event = threading.Event()
        stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _ensure_worker(self) -> None:
        """Start the consumer if needed. Called with ``self._lock`` held."""
        if (
            self._thread is not None
            and self._thread.is_alive()
            and self._thread_stop is self._stop_event
        ):
            return
        stop_event = self._stop_event
        self._thread_stop = stop_event
        self._thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            name=self._name,
            daemon=True,
        )
        self._thread.start()

    def _run(self, stop_event: threading.Event) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if stop_event.is_set():
                    return
                continue
            try:
                job, key, timeout = item
                self._execute(job, key, timeout)
            finally:
                self._queue.task_done()

    def _execute(
        self, job: Callable[[], Any], key: Optional[Any], timeout: Optional[float]
    ) -> None:
        """Run one job off-consumer with a deadline and an active-job cap."""
        deadline = self._job_timeout if timeout is None else max(0.0, float(timeout))
        with self._lock:
            if self._active_jobs >= self._max_active_jobs:
                if key is not None:
                    self._pending_keys.discard(key)
                logger.warning(f"{self._name} active job cap reached; job dropped")
                return
            self._active_jobs += 1

        done = threading.Event()

        def runner() -> None:
            try:
                job()
            except Exception as e:
                logger.warning(f"{self._name} job failed: {e}")
            finally:
                done.set()
                with self._lock:
                    self._active_jobs -= 1
                    if key is not None:
                        self._pending_keys.discard(key)

        worker = threading.Thread(
            target=runner,
            name=f"{self._name}-job",
            daemon=True,
        )
        worker.start()
        if not done.wait(deadline):
            logger.warning(
                f"{self._name} job exceeded {deadline:g}s timeout; continuing "
                "without waiting"
            )


_live_dispatchers = []
_live_lock = threading.Lock()
_atexit_registered = False


def _drain_all_dispatchers() -> None:
    with _live_lock:
        pending = list(_live_dispatchers)
        _live_dispatchers.clear()
    for dispatcher in pending:
        try:
            dispatcher.drain()
        except Exception as e:
            logger.debug(f"Dispatcher drain failed during shutdown: {e}")


def get_notification_dispatcher() -> BoundedBackgroundDispatcher:
    """Process-wide dispatcher for notification fan-out and push batches."""
    global _shared_dispatcher, _atexit_registered
    with _live_lock:
        if _shared_dispatcher is None:
            _shared_dispatcher = BoundedBackgroundDispatcher("notification-dispatch")
            _live_dispatchers.append(_shared_dispatcher)
            if not _atexit_registered:
                atexit.register(_drain_all_dispatchers)
                _atexit_registered = True
        return _shared_dispatcher


_shared_dispatcher: Optional[BoundedBackgroundDispatcher] = None
