import sqlite3
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional, Dict, List, Tuple
from .base import BaseEngine

import utils.logger as logger

DEFAULT_BUSY_TIMEOUT_MS = 5000
# The periodic monitor reclaims idle checked-out connections on a longer
# window than the exhaustion-time reclaim, so the middleware's per-request-
# thread keep-alive connections are not churned on every monitor tick.
MONITOR_IDLE_RECLAIM_SECONDS = 60.0
# A checked-out connection inside a transaction is only treated as "live"
# (and therefore protected from reclaim) while it has been idle for less than
# this window. Beyond it the owner is presumed gone — even if its thread ident
# was recycled to a new thread — so the abandoned transaction's locks and pool
# capacity are reclaimed instead of leaking forever. No real SQLite
# transaction pauses between statements for this long.
IN_TRANSACTION_ALIVE_GRACE_SECONDS = 60.0

sqlite3.register_adapter(datetime, lambda dt: dt.isoformat(sep=" "))


class SqlitePool:
    """Simple thread-safe pool for SQLite connections.

    Beyond the classic idle/active accounting, every checked-out connection is
    tracked with its last-usage time (refreshed by ``touch`` from the Database
    layer on every query) and its owning thread. This lets the pool reclaim
    connections that were checked out into thread-local storage and never
    returned — e.g. by an AnyIO ``run_in_threadpool`` worker that ran a single
    query — instead of letting them permanently starve the pool.
    """

    def __init__(
        self,
        create_conn,
        min_size: int,
        max_size: int,
        idle_reclaim_seconds: float = 5.0,
    ):
        self._create_conn = create_conn
        self._min_size = min_size
        self._max_size = max_size
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
                        "SQLite connection pool exhausted: no available connections"
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
        executing thread would observe a sudden "closed database" failure
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


class SqliteEngine(BaseEngine):
    """SQLite database engine implementation."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._pool: Optional[SqlitePool] = None
        self._pool_config = config.get("connection_pool", {})

    def _create_single_connection(self) -> sqlite3.Connection:
        db_path = self.config.get("path", "data/database.db")

        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)

        conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        busy_timeout_ms = self._pool_config.get("busy_timeout", DEFAULT_BUSY_TIMEOUT_MS)
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)};")

        conn.execute("PRAGMA foreign_keys=ON;")

        # These PRAGMAs control per-connection memory. The defaults are
        # tuned for the production server; tests override them via the
        # ``connection_pool`` config (``cache_size_kb``, ``mmap_size``,
        # ``journal_size_limit``) so thousands of per-test pools stay tiny
        # instead of reserving ~32MB page cache + 256MB mmap each.
        cache_size_kb = self._pool_config.get("cache_size_kb", -32000)
        conn.execute(f"PRAGMA cache_size={int(cache_size_kb)};")

        mmap_size = self._pool_config.get("mmap_size", 268435456)
        conn.execute(f"PRAGMA mmap_size={int(mmap_size)};")

        temp_store = self._pool_config.get("temp_store", "MEMORY")
        conn.execute(f"PRAGMA temp_store={temp_store};")

        journal_size_limit = self._pool_config.get("journal_size_limit", 67108864)
        conn.execute(f"PRAGMA journal_size_limit={int(journal_size_limit)};")

        return conn

    def create_pool(self, min_conn: int, max_conn: int) -> SqlitePool:
        logger.info(f"Creating SQLite connection pool (min={min_conn}, max={max_conn})")
        start_time = time.time()
        try:
            pool = SqlitePool(
                create_conn=self._create_single_connection,
                min_size=min_conn,
                max_size=max_conn,
                idle_reclaim_seconds=float(
                    self._pool_config.get("idle_reclaim_seconds", 5.0)
                ),
            )
            elapsed = time.time() - start_time
            logger.info(
                f"SQLite connection pool created successfully in {elapsed:.2f}s"
            )
            self._pool = pool
            return pool
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"SQLite connection pool creation FAILED after {elapsed:.2f}s: {e}"
            )
            raise

    def connect(self, pool: Optional[Any] = None) -> sqlite3.Connection:
        if pool is not None and isinstance(pool, SqlitePool):
            # Honor the configured ``connect_timeout`` (default 30s) so pool
            # exhaustion surfaces fast instead of every thread blocking the
            # hardcoded 30s default.
            timeout = self._pool_config.get("connect_timeout", 30.0)
            return pool.get_connection(timeout=timeout)
        return self._create_single_connection()

    def get_pool_stats(self, pool: Any) -> Dict[str, Any]:
        stats: Dict[str, Any]
        if isinstance(pool, SqlitePool):
            stats = pool.get_stats()
        else:
            stats = {
                "active_connections": 0,
                "idle_connections": 0,
                "total_connections": 0,
                "max_connections": 0,
                "min_connections": 0,
                "utilization_percent": 0,
            }

        try:
            stats["database_type"] = "sqlite"
            stats["database_path"] = self.config.get("path", "data/database.db")
        except Exception as e:
            logger.debug(f"Could not retrieve SQLite stats: {e}")

        return stats

    def get_detailed_stats(
        self, conn: Optional[sqlite3.Connection] = None
    ) -> Dict[str, Any]:
        stats = {
            "database_type": "sqlite",
            "database_path": self.config.get("path", "data/database.db"),
            "page_size": 0,
            "page_count": 0,
            "database_size_bytes": 0,
            "wal_size_bytes": 0,
            "checkpoint_count": 0,
        }

        if not conn:
            return stats

        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA page_size;")
            stats["page_size"] = cursor.fetchone()[0] or 0

            cursor.execute("PRAGMA page_count;")
            stats["page_count"] = cursor.fetchone()[0] or 0

            stats["database_size_bytes"] = stats["page_size"] * stats["page_count"]

            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0] or ""
            if journal_mode.upper() == "WAL":
                db_path = self.config.get("path", "data/database.db")
                wal_path = f"{db_path}-wal"
                if os.path.exists(wal_path):
                    stats["wal_size_bytes"] = os.path.getsize(wal_path)

            cursor.close()
        except Exception as e:
            logger.debug(f"Could not retrieve detailed SQLite stats: {e}")

        return stats

    def close_connection(
        self,
        conn: Any,
        pool: Optional[Any] = None,
        close_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not conn:
            return

        if isinstance(pool, SqlitePool):
            should_close = close_params.get("close", False) if close_params else False
            pool.return_connection(conn, force_close=should_close)
        else:
            try:
                conn.close()
            except Exception as e:
                logger.debug(f"Could not close connection: {e}")

    def get_table_exists_query(self, table_name: str) -> Tuple[str, Tuple]:
        return "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (
            table_name,
        )

    def get_insert_or_ignore_query(self, table: str, columns: List[str]) -> str:
        safe_cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        return f"INSERT OR IGNORE INTO {table} ({safe_cols}) VALUES ({placeholders})"

    def get_upsert_query(
        self,
        table: str,
        columns: List[str],
        conflict_columns: List[str],
        update_columns: Optional[List[str]] = None,
    ) -> str:
        safe_cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        conflict_cols = ", ".join(conflict_columns)
        if update_columns:
            updates = ", ".join([f"{c} = excluded.{c}" for c in update_columns])
            return f"INSERT INTO {table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"
        return f"INSERT OR REPLACE INTO {table} ({safe_cols}) VALUES ({placeholders})"
