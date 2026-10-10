"""Bounded background job dispatch for notification fan-out and push delivery.

A single long-lived consumer thread per dispatcher drains a bounded
``queue.Queue`` of callables. Jobs are dropped (never block the caller)
when the queue is full — notification work is strictly best-effort and
must never back-pressure the message-send path.

Shutdown: every dispatcher created through :func:`get_notification_dispatcher`
is registered once with ``atexit`` so pending jobs are drained and the
consumer thread is joined on interpreter exit.
"""

import atexit
import os
import queue
import threading
from typing import Optional

import utils.logger as logger

DEFAULT_QUEUE_BOUND = 1000
SHUTDOWN_DRAIN_TIMEOUT = 5.0

SYNC_DISPATCH_ENV = "PLEXICHAT_SYNC_NOTIFICATION_DISPATCH"


def is_sync_notification_dispatch() -> bool:
    """True when notification dispatch should run inline (tests/embedded)."""
    return os.environ.get(SYNC_DISPATCH_ENV, "") not in ("", "0")


class BoundedBackgroundDispatcher:
    """Single-consumer worker draining a bounded queue of callables."""

    def __init__(self, name: str, queue_bound: int = DEFAULT_QUEUE_BOUND):
        self._name = name
        self._queue: "queue.Queue" = queue.Queue(maxsize=queue_bound)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def submit(self, job) -> bool:
        """Enqueue ``job`` for the consumer thread. False when queue full."""
        if job is None:
            return False
        self._ensure_worker()
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            return False

    def pending(self) -> int:
        return self._queue.qsize()

    def drain(self, timeout: float = SHUTDOWN_DRAIN_TIMEOUT) -> None:
        """Flush pending jobs and join the worker. Safe to call repeatedly."""
        with self._lock:
            thread = self._thread
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name=self._name,
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                job()
            except Exception as e:
                logger.warning(f"{self._name} job failed: {e}")
            finally:
                self._queue.task_done()


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
