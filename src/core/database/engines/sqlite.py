import sqlite3
import os
import time
from datetime import datetime
from typing import Any, Optional, Dict, List, Tuple
from src.core.security.hardening import bounded_int

from ..pool_budget import resolve_connect_timeout, resolve_pool_limits
from .base import BaseEngine
from .pool import (
    ConnectionPool,
    MONITOR_IDLE_RECLAIM_SECONDS,
    IN_TRANSACTION_ALIVE_GRACE_SECONDS,
)

import utils.logger as logger

__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "MONITOR_IDLE_RECLAIM_SECONDS",
    "IN_TRANSACTION_ALIVE_GRACE_SECONDS",
    "SqlitePool",
    "SqliteEngine",
]

DEFAULT_BUSY_TIMEOUT_MS = 5000

sqlite3.register_adapter(datetime, lambda dt: dt.isoformat(sep=" "))


class SqlitePool(ConnectionPool):
    """Connection pool for SQLite (the shared driver-agnostic implementation).

    The reclaim/accounting logic lives in :class:`~..pool.ConnectionPool` so
    the PostgreSQL engine reuses it; only the label differs.
    """

    def __init__(
        self,
        create_conn,
        min_size: int,
        max_size: int,
        idle_reclaim_seconds: float = 5.0,
    ):
        super().__init__(
            create_conn=create_conn,
            min_size=min_size,
            max_size=max_size,
            idle_reclaim_seconds=idle_reclaim_seconds,
            label="SQLite",
        )


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
        conn.execute(
            "PRAGMA busy_timeout="
            f"{bounded_int(busy_timeout_ms, 'busy_timeout', minimum=0, maximum=600000)};"
        )

        conn.execute("PRAGMA foreign_keys=ON;")

        # These PRAGMAs control per-connection memory. The defaults are
        # tuned for the production server; tests override them via the
        # ``connection_pool`` config (``cache_size_kb``, ``mmap_size``,
        # ``journal_size_limit``) so thousands of per-test pools stay tiny
        # instead of reserving ~32MB page cache + 256MB mmap each.
        cache_size_kb = self._pool_config.get("cache_size_kb", -32000)
        conn.execute(
            "PRAGMA cache_size="
            f"{bounded_int(cache_size_kb, 'cache_size_kb', minimum=-2000000, maximum=2000000)};"
        )

        mmap_size = self._pool_config.get("mmap_size", 268435456)
        conn.execute(
            "PRAGMA mmap_size="
            f"{bounded_int(mmap_size, 'mmap_size', minimum=0, maximum=1099511627776)};"
        )

        # ``temp_store`` is interpolated, so it must be one of the three
        # documented values rather than whatever the config carries.
        temp_store = str(self._pool_config.get("temp_store", "MEMORY")).upper()
        if temp_store not in ("MEMORY", "FILE", "OFF"):
            raise ValueError("connection_pool.temp_store must be MEMORY, FILE or OFF")
        conn.execute(f"PRAGMA temp_store={temp_store};")

        journal_size_limit = self._pool_config.get("journal_size_limit", 67108864)
        conn.execute(
            "PRAGMA journal_size_limit="
            f"{bounded_int(journal_size_limit, 'journal_size_limit', minimum=-1, maximum=1099511627776)};"
        )

        return conn

    def create_pool(self, min_conn: int, max_conn: int) -> SqlitePool:
        # Every ``--scale backend=N`` replica builds its own pool from the same
        # config. Apply the same fleet-wide budget division the PostgreSQL
        # engine uses: SQLite handles reserve mmap/page-cache memory each, so
        # N replicas opening N full-size pools is a real memory blow-up. The
        # helper also orders min/max and rejects hostile values before they
        # reach the pool constructor.
        limits = resolve_pool_limits(
            self.config, requested_min=min_conn, requested_max=max_conn
        )
        min_conn = limits.min_connections
        max_conn = limits.max_connections
        connect_timeout = resolve_connect_timeout(self.config, default=30.0)
        logger.info(
            f"Creating SQLite connection pool (min={min_conn}, max={max_conn}) "
            f"with timeout {connect_timeout}s"
        )
        start_time = time.time()

        # ``SqlitePool.__init__`` opens ``min_conn`` warm connections eagerly.
        # If one of them fails part-way through, the already-opened handles in
        # the half-built pool are otherwise leaked. Track everything created
        # for construction and close it on the error path, then stop tracking
        # so the wrapper does not retain every handle the pool opens later.
        created: List[sqlite3.Connection] = []
        tracking = True

        def _tracked_create() -> sqlite3.Connection:
            conn = self._create_single_connection()
            if tracking:
                created.append(conn)
            return conn

        try:
            pool = SqlitePool(
                create_conn=_tracked_create,
                min_size=min_conn,
                max_size=max_conn,
                idle_reclaim_seconds=float(
                    self._pool_config.get("idle_reclaim_seconds", 5.0)
                ),
            )
        except Exception as e:
            for conn in created:
                try:
                    conn.close()
                except Exception as close_error:
                    logger.debug(f"SQLite pool cleanup close failed: {close_error}")
            elapsed = time.time() - start_time
            logger.error(
                f"SQLite connection pool creation FAILED after {elapsed:.2f}s: {e}"
            )
            raise
        finally:
            tracking = False

        # The pool owns the warm connections now; drop the temporary references
        # still captured by the tracking closure.
        created.clear()
        elapsed = time.time() - start_time
        logger.info(f"SQLite connection pool created successfully in {elapsed:.2f}s")
        self._pool = pool
        return pool

    def connect(self, pool: Optional[Any] = None) -> sqlite3.Connection:
        if pool is not None and isinstance(pool, SqlitePool):
            # Clamp the configured timeout at the boundary: a raw config value
            # may be negative, NaN or enormous, which would park a request
            # thread forever on an exhausted pool. Keep parity with the
            # PostgreSQL engine's bounded acquire path.
            timeout = resolve_connect_timeout(self.config, default=30.0)
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
