"""Driver-agnostic connection pool shared by the SQLite and PostgreSQL engines.

Extracted from the original ``SqlitePool`` so the PostgreSQL engine (pg8000)
reuses one proven implementation instead of carrying a second copy of the same
idle/transaction/in-flight reclaim logic. The pool only ever calls
``create_conn()`` and ``conn.close()``, so it works with any DB-API driver.
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

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

        for _ in range(min_size):
            self._available.append(self._create_conn())
            self._total_created += 1

    def get_connection(self, timeout: float = 30.0) -> Any:
        deadline = time.monotonic() + timeout
        with self._condition:
            if not self._available and self._total_created >= self._max_size:
                # Exhausted: before blocking for the connect timeout, reclaim
                # checked-out connections that have gone idle (the thread-local
                # leak case) so capacity is restored promptly.
                self._reap_idle_unlocked()
            while not self._available and self._total_created >= self._max_size:
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
            except Exception:
                pass
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
                self._condition.notify()
                return
            if force_close:
                try:
                    conn.close()
                except Exception:
                    pass
                self._total_created -= 1
                self._condition.notify()
                return

            if len(self._available) < self._max_size:
                self._available.append(conn)
                self._condition.notify()
            else:
                try:
                    conn.close()
                except Exception:
                    pass
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
            except Exception:
                pass

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
            }
