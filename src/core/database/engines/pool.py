"""Driver-agnostic connection pool shared by the SQLite and PostgreSQL engines.

Extracted from the original ``SqlitePool`` so the PostgreSQL engine (pg8000)
reuses one proven implementation instead of carrying a second copy of the same
idle/transaction/in-flight reclaim logic. The pool only ever calls
``create_conn()`` and ``conn.close()``, so it works with any DB-API driver.
"""

import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.security.hardening import bounded_int

#: Hard ceiling on connections a single pool may hold. ``min_size`` connections
#: are created synchronously in ``__init__``, so an unbounded config value here
#: is a startup-time resource-exhaustion primitive, not a harmless typo.
MAX_POOL_SIZE = 10_000

# The periodic monitor reclaims idle checked-out connections on a longer
# window than the exhaustion-time reclaim, so the middleware's per-request-
# thread keep-alive connections are not churned on every monitor tick.
MONITOR_IDLE_RECLAIM_SECONDS = 60.0
# A checked-out connection inside a transaction is only treated as "live"
# (and therefore protected from reclaim) while it has been idle for less than
# this window. Beyond it the owner is presumed gone — even if its thread ident
# was recycled to a new thread — so the abandoned transaction's locks and pool
# capacity are reclaimed instead of leaking forever. No real SQLite/PostgreSQL
# transaction pauses between statements for this long.
IN_TRANSACTION_ALIVE_GRACE_SECONDS = 60.0
MAX_ACQUIRE_TIMEOUT_SECONDS = 300.0


def _bounded_acquire_timeout(timeout) -> float:
    """Clamp a configured pool acquisition timeout to a sane range.

    An unbounded value parks a request thread forever when the pool is
    exhausted; a negative one makes every acquisition fail immediately.
    """
    try:
        return min(max(float(timeout), 0.0), MAX_ACQUIRE_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        return 30.0


class ConnectionPool:
    """Thread-safe pool for DB-API connections.

    Beyond the classic idle/active accounting, every checked-out connection is
    tracked with its last-usage time (refreshed by ``touch`` from the Database
    layer on every query) and its owning thread. This lets the pool reclaim
    connections that were checked out into thread-local storage and never
    returned — e.g. by an AnyIO ``run_in_threadpool`` worker that ran a single
    query — instead of letting them permanently starve the pool.

    ``label`` names the engine in the exhaustion message (``"SQLite"`` /
    ``"PostgreSQL"``) so operators can tell which pool is saturated.
    """

    def __init__(
        self,
        create_conn,
        min_size: int,
        max_size: int,
        idle_reclaim_seconds: float = 5.0,
        label: str = "database",
    ):
        # ``min_size`` connections are opened in the loop below before the
        # constructor returns. Bound both sizes and keep them ordered so a
        # mis-set (or hostile) config cannot allocate an unbounded number of
        # sockets/databases on the first request, nor spin forever waiting for
        # a pool whose max is below its min.
        min_size = bounded_int(min_size, "min_size", minimum=0, maximum=MAX_POOL_SIZE)
        max_size = bounded_int(max_size, "max_size", minimum=1, maximum=MAX_POOL_SIZE)
        if min_size > max_size:
            raise ValueError("min_size cannot exceed max_size")

        self._create_conn = create_conn
        self._min_size = min_size
        self._max_size = max_size
        self._label = label
        # Checked-out connections idle longer than this are reclaimed when the
        # pool is exhausted (or on a periodic reap) instead of blocking callers
        # for the full connect timeout.
        self._idle_reclaim_seconds = idle_reclaim_seconds
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._available: List[Any] = []
        self._in_use: set = set()
        self._by_id: Dict[int, Any] = {}
        # id(conn) -> (monotonic last-usage, owner thread ident,
        # in-transaction flag, cursor-in-flight flag)
        self._checked_out: Dict[int, Tuple[float, Optional[int], bool, bool]] = {}
        self._total_created = 0
        self._closed = False

        # FIFO queue of waiters. ``Condition.notify()`` wakes an arbitrary
        # waiter, which under sustained contention lets later arrivals bypass
        # threads that have been parked longer (and can starve them). Only the
        # head of this queue may consume a newly returned connection.
        self._wait_queue: "deque[Any]" = deque()

        # Instrumented wait cost: every caller that had to queue records how
        # long it blocked, with an optional observer wired to the monitor.
        self._wait_count = 0
        self._wait_total = 0.0
        self._wait_max = 0.0
        self._on_wait: Optional[Callable[[float], None]] = None

        for _ in range(min_size):
            self._available.append(self._create_conn())
            self._total_created += 1

    def set_wait_observer(self, callback: Optional[Callable[[float], None]]) -> None:
        """Set the sink for observed FIFO wait durations (in seconds).

        Metrics are a side channel: a failing observer must never abort a
        connection acquisition, so the callback is invoked defensively.
        """
        with self._condition:
            self._on_wait = callback

    def _record_wait(self, duration: float) -> None:
        """Record one completed wait. Caller holds ``self._condition``."""
        self._wait_count += 1
        self._wait_total += duration
        self._wait_max = max(self._wait_max, duration)
        observer = self._on_wait
        if observer is not None:
            try:
                observer(duration)
            except Exception:
                # A broken metrics sink must not break pool acquisitions.
                pass

    def get_connection(self, timeout: float = 30.0) -> Any:
        deadline = time.monotonic() + _bounded_acquire_timeout(timeout)
        with self._condition:
            if not self._available and self._total_created >= self._max_size:
                # Exhausted: before blocking for the connect timeout, reclaim
                # checked-out connections that have gone idle (the thread-local
                # leak case) so capacity is restored promptly.
                self._reap_idle_unlocked()

            # Register before blocking so a return that happens between the
            # capacity check and the first wait() cannot be missed.
            token: Any = None
            wait_started: Optional[float] = None
            if not self._available and self._total_created >= self._max_size:
                token = object()
                self._wait_queue.append(token)
                wait_started = time.monotonic()

            try:
                while True:
                    has_capacity = (
                        bool(self._available) or self._total_created < self._max_size
                    )
                    is_head = token is None or (
                        bool(self._wait_queue) and self._wait_queue[0] is token
                    )
                    if has_capacity and is_head:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"{self._label} connection pool exhausted: "
                            "no available connections"
                        )
                    self._condition.wait(remaining)

                if self._available:
                    conn = self._available.pop()
                else:
                    conn = self._create_conn()
                    self._total_created += 1

                # A newly created connection may leave free slots for the
                # waiters still queued behind this one; let them re-check.
                if self._wait_queue and self._total_created < self._max_size:
                    self._condition.notify_all()
            finally:
                if token is not None:
                    try:
                        self._wait_queue.remove(token)
                    except ValueError:  # already removed (defensive)
                        pass
                    if wait_started is not None:
                        self._record_wait(time.monotonic() - wait_started)
                    # Hand the turn to the next waiter in FIFO order.
                    self._condition.notify_all()

            conn_id = id(conn)
            self._in_use.add(conn_id)
            self._by_id[conn_id] = conn
            self._checked_out[conn_id] = (
                time.monotonic(),
                threading.current_thread().ident,
                False,
                False,
            )
            return conn

    def touch(self, conn: Any, in_transaction: bool = False) -> None:
        """Record that ``conn`` was just used (called per query by the DB layer).

        Keeps actively-used connections from being reclaimed as idle.
        ``in_transaction`` marks a connection whose owner is inside a
        transaction, so an alive owner's open transaction is never reclaimed
        mid-flight (that would abort it).
        """
        conn_id = id(conn)
        with self._condition:
            usage = self._checked_out.get(conn_id)
            if usage is not None:
                self._checked_out[conn_id] = (
                    time.monotonic(),
                    usage[1],
                    bool(in_transaction),
                    usage[3],
                )

    def mark_in_flight(self, conn: Any, in_flight: bool = True) -> None:
        """Mark ``conn`` as having a cursor mid-statement.

        While the flag is set the idle reaper NEVER closes the connection,
        so a long-running statement (> ``idle_reclaim_seconds``) cannot be
        closed underneath its executing thread. The Database layer sets this
        for the duration of every cursor.execute() call.
        """
        conn_id = id(conn)
        with self._condition:
            usage = self._checked_out.get(conn_id)
            if usage is not None:
                self._checked_out[conn_id] = (
                    usage[0],
                    usage[1],
                    usage[2],
                    bool(in_flight),
                )

    def _reap_idle_unlocked(self, max_idle_seconds: Optional[float] = None) -> int:
        """Close checked-out connections idle past the reclaim threshold.

        Caller must hold ``self._condition``. The current thread's own
        connection is never reclaimed, and an owner thread whose connection
        was reclaimed transparently reconnects on its next use (``_get_conn``
        detects the closed connection and rebuilds it).

        A connection whose owner is mid-query (cursor in flight, marked via
        ``mark_in_flight``) is NEVER reclaimed regardless of idle time: the
        executing thread would observe a sudden "connection is closed" failure
        and non-idempotent statements (INSERT) could not be safely retried.

        Connections inside an open transaction whose owner thread is still
        alive are NEVER reclaimed (reclaiming would abort the transaction).
        An abandoned transaction whose owner thread has exited is reclaimed so
        its locks and pool capacity are released.
        """
        threshold = (
            self._idle_reclaim_seconds if max_idle_seconds is None else max_idle_seconds
        )
        now = time.monotonic()
        current_ident = threading.current_thread().ident
        reaped = 0
        for conn_id, (last_used, owner_ident, in_transaction, in_flight) in list(
            self._checked_out.items()
        ):
            if in_flight:
                # Statement currently executing on this connection.
                continue
            if owner_ident == current_ident:
                continue
            if now - last_used < threshold:
                continue
            if in_transaction and self._thread_alive(owner_ident):
                # Protect a live owner's open transaction, but only within a
                # generous grace window (see IN_TRANSACTION_ALIVE_GRACE_SECONDS).
                if now - last_used < IN_TRANSACTION_ALIVE_GRACE_SECONDS:
                    continue
            # Pop both trackers together so a missing ``_by_id`` entry (should
            # never happen) cannot leave a zombie entry retried forever.
            conn = self._by_id.pop(conn_id, None)
            self._checked_out.pop(conn_id, None)
            if conn is None:
                continue
            self._in_use.discard(conn_id)
            try:
                conn.close()
            except Exception as close_error:
                # The connection is leaving the pool either way; report the leak
                # source rather than hiding it.
                import utils.logger as logger

                logger.debug(f"pool: reaped connection close failed: {close_error}")
            self._total_created -= 1
            reaped += 1
        if reaped:
            self._condition.notify_all()
        return reaped

    def reap_idle(self, max_idle_seconds: Optional[float] = None) -> int:
        """Reclaim idle checked-out connections (thread-safe public wrapper).

        ``max_idle_seconds`` overrides the configured threshold; the periodic
        monitor passes a longer window than the exhaustion-time reclaim so
        keep-alive connections on request threads are not churned.
        """
        with self._condition:
            return self._reap_idle_unlocked(max_idle_seconds)

    @staticmethod
    def _thread_alive(ident: Optional[int]) -> bool:
        """Return True if the thread with the given ident is still running."""
        if ident is None:
            return False
        try:
            return any(t.ident == ident and t.is_alive() for t in threading.enumerate())
        except Exception:
            return False

    def return_connection(self, conn: Any, force_close: bool = False) -> None:
        with self._condition:
            conn_id = id(conn)
            tracked = conn_id in self._in_use
            self._in_use.discard(conn_id)
            self._by_id.pop(conn_id, None)
            self._checked_out.pop(conn_id, None)
            if not tracked:
                # Already reclaimed (closed) by ``_reap_idle_unlocked`` —
                # nothing left to return or account for.
                self._condition.notify_all()
                return
            if force_close:
                try:
                    conn.close()
                except Exception as close_error:
                    import utils.logger as logger

                    logger.debug(f"pool: forced connection close failed: {close_error}")
                self._total_created -= 1
                self._condition.notify_all()
                return

            if len(self._available) < self._max_size:
                self._available.append(conn)
                # Wake every waiter: the FIFO head check decides who proceeds.
                self._condition.notify_all()
            else:
                try:
                    conn.close()
                except Exception as close_error:
                    import utils.logger as logger

                    logger.debug(f"pool: idle connection close failed: {close_error}")
                self._total_created -= 1

    def close(self) -> None:
        """Close every pooled connection and mark the pool closed.

        Called on ``Database.reset()`` / shutdown. Idempotent and
        exception-safe: a connection that is already gone must not stop the
        rest from being closed.
        """
        with self._condition:
            if self._closed:
                return
            self._closed = True
            conns = list(self._available) + list(self._by_id.values())
            self._available.clear()
            self._by_id.clear()
            self._checked_out.clear()
            self._in_use.clear()
            self._total_created = 0
            self._condition.notify_all()

        for conn in conns:
            try:
                conn.close()
            except Exception as close_error:
                import utils.logger as logger

                logger.debug(f"pool: connection closeall entry failed: {close_error}")

    # ``Database.reset()`` still calls ``closeall()`` as a fallback for
    # psycopg2-shaped mocks; keep the alias so that path stays quiet.
    closeall = close

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            active = len(self._in_use)
            idle = len(self._available)
            total = self._total_created
            return {
                "active_connections": active,
                "idle_connections": idle,
                "total_connections": total,
                "max_connections": self._max_size,
                "min_connections": self._min_size,
                "utilization_percent": (active / self._max_size * 100)
                if self._max_size > 0
                else 0.0,
                "wait_count": self._wait_count,
                "avg_wait_time": (self._wait_total / self._wait_count)
                if self._wait_count
                else 0.0,
                "max_wait_time": self._wait_max,
                "wait_queue_depth": len(self._wait_queue),
            }
