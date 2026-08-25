"""
Database module - Provides database connectivity for SQLite and PostgreSQL.

This module follows the zero-friction pattern established by common_utils.
It acts as a facade, delegating to engine-specific, monitoring, and dialect components.
"""

import os
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

import utils.config as config
import utils.logger as logger

from ..engines.base import BaseEngine
from ..engines.sqlite import SqliteEngine
from ..engines.postgres import PostgresEngine
from ..monitoring import DatabaseMonitor
from ..builder.composer import QueryBuilder
from .connection import DatabaseConnectionMixin
from .execution import DatabaseExecutionMixin
from .maintenance import DatabaseMaintenanceMixin
from .metrics import DatabaseMetricsMixin
from .transactions import DatabaseTransactionMixin
from .types import DatabaseLocal, DbConnection


class Database(
    DatabaseConnectionMixin,
    DatabaseExecutionMixin,
    DatabaseTransactionMixin,
    DatabaseMaintenanceMixin,
    DatabaseMetricsMixin,
):
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        # Thread-safe one-time init. ``__new__`` already serialises instance
        # creation via ``_instance_lock``, but ``__init__`` runs once per
        # caller unless guarded — concurrent first-use (e.g. 40 load-test
        # threads each calling ``Database()`` at once) would otherwise race
        # on ``self.engine``/``self.monitor``/``self._pool`` assignments and
        # spawn several monitor threads, crashing the interpreter.
        with self._instance_lock:
            if getattr(self, "_initialized", False):
                return
            self._init_once()

    def _init_once(self):
        db_config = config.get("database")

        if db_config is None:
            logger.warning("Database configuration not found. Using default SQLite.")
            db_config = {"type": "sqlite", "path": "data/database.db"}
        elif not isinstance(db_config, dict):
            logger.error("Database configuration is malformed. Using default SQLite.")
            db_config = {"type": "sqlite", "path": "data/database.db"}

        self.config: Dict[str, Any] = db_config
        self.type: str = self.config.get("type", "sqlite")

        self._pool = None
        self._local = DatabaseLocal()
        self._lock = threading.RLock()

        self._query_cache: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._query_cache_ttl = 1.0
        self._query_cache_lock = threading.RLock()

        self._in_transaction = False

        self.engine: BaseEngine
        if self.type == "postgres":
            self.engine = PostgresEngine(self.config)
            self._pool = None
            logger.info("PostgreSQL engine initialized (pool creation deferred)")
        elif self.type == "sqlite":
            self.engine = SqliteEngine(self.config)
        else:
            raise ValueError(f"Unsupported database type: {self.type}")

        self.monitor = DatabaseMonitor(self.config, self.type)

        monitoring_config = config.get("monitoring", {})
        alert_thresholds = monitoring_config.get("alert_thresholds", {})
        self._slow_query_threshold_ms = alert_thresholds.get("query_time_ms", 5000)

        pool_config = self.config.get("connection_pool", {})
        self._enable_validation = pool_config.get("enable_validation", True)
        self._validation_query = pool_config.get("validation_query", "SELECT 1")
        self._validation_interval = pool_config.get("validation_interval", 60)
        self._max_idle_time = pool_config.get("max_idle_time", 1800)

        max_age_hours = pool_config.get("max_connection_age_hours", 2.0)
        self._max_connection_age_seconds = max_age_hours * 3600

        logger.info(f"Database initialized with type: {self.type}")
        # Disposable-DB flag (test/CI targets only). Computed at DB init from
        # the instance config (``database.disposable``) or an EXTERNALLY set
        # ``PLEXICHAT_DISPOSABLE_DB=1`` environment marker; the server never
        # writes the variable itself, so no process-wide state is polluted —
        # consumers consult the ``Database.disposable`` attribute instead.
        self.disposable: bool = bool(self.config.get("disposable")) or (
            os.environ.get("PLEXICHAT_DISPOSABLE_DB") == "1"
        )
        if self.disposable:
            try:
                from src.core.selftest.db_guard import write_disposable_marker

                write_disposable_marker(self)
            except Exception as e:
                logger.debug(f"Disposable marker persistence skipped: {e}")
        if self.type in ("postgres", "sqlite"):
            self.start_pool_monitoring()
        self._initialized = True

    @classmethod
    def reset(cls) -> None:
        """Tear down the singleton so the next ``Database()`` re-initialises.

        The process-wide singleton pattern (``__new__`` caches ``_instance``
        and ``__init__`` runs only once) means the engine, connection pool,
        and pool-monitor thread are bound to whatever config the first
        construction saw. This is a problem for tests that re-point
        ``config`` per test: every later ``Database()`` returned the stale
        instance and silently kept using the old path/engine, so tests that
        register hardcoded usernames saw the previous test's rows and tests
        that swapped to a new DB path kept connecting to the old one.

        ``reset()`` stops the monitor thread, closes every pooled and
        thread-local connection, clears connection metadata, and drops the
        cached instance so the next ``Database()`` builds a fresh engine +
        pool against the current config. Safe to call at any time (it is
        also what keeps repeated test resets from leaking one pool + one
        monitor thread per test).
        """
        with cls._instance_lock:
            inst = cls._instance
            if inst is None:
                return
            try:
                inst.stop_pool_monitoring()
            except Exception:
                pass

            # Close EVERY known connection: the pool's idle set, the current
            # thread's checked-out connection, and — via the monitor's
            # metadata — connections checked out by OTHER threads that the
            # pool no longer tracks in ``_available``. Without this last
            # pass, ``reset()`` would leak those open sqlite3 handles.
            seen = set()
            pool = getattr(inst, "_pool", None)
            if pool is not None:
                if type(pool).__name__ == "SqlitePool":
                    # sqlite pool: close idle connections and reset counters.
                    # Checked-out connections (incl. other threads') are
                    # closed below via the monitor's connection metadata.
                    try:
                        with pool._lock:
                            for conn in list(pool._available):
                                try:
                                    conn.close()
                                except Exception:
                                    pass
                                seen.add(id(conn))
                            pool._available.clear()
                            pool._in_use.clear()
                            getattr(pool, "_by_id", {}).clear()
                            getattr(pool, "_checked_out", {}).clear()
                            pool._total_created = 0
                            # Wake threads blocked in ``get_connection`` on
                            # this pool (e.g. a worker mid-query during
                            # shutdown). Without a notify they sleep the full
                            # ``connect_timeout`` (30s default) while holding
                            # their thread slot, stalling shutdown and — in
                            # the test suite — making later tests queue 30s
                            # behind them.
                            pool._condition.notify_all()
                    except Exception:
                        pass
                else:
                    # psycopg2 ThreadedConnectionPool (or mock): close all
                    # pooled connections properly instead of silently
                    # dropping them via sqlite-only attribute access.
                    try:
                        pool.closeall()
                    except Exception:
                        try:
                            for conn in list(getattr(pool, "_pool", [])):
                                try:
                                    conn.close()
                                except Exception:
                                    pass
                        except Exception:
                            pass
            local = getattr(inst, "_local", None)
            if local is not None:
                old_conn = getattr(local, "connection", None)
                if old_conn is not None:
                    try:
                        old_conn.close()
                    except Exception:
                        pass
                    seen.add(id(old_conn))
                local.connection = None
                local.transaction_depth = 0
                local.in_transaction = False
                local.last_insert_rowid = None
            monitor = getattr(inst, "monitor", None)
            if monitor is not None:
                try:
                    for conn_id, meta in list(monitor._connection_metadata.items()):
                        if conn_id in seen:
                            continue
                        conn = meta.get("connection")
                        if conn is not None:
                            try:
                                conn.close()
                            except Exception:
                                pass
                    monitor._connection_metadata.clear()
                except Exception:
                    pass
            # Query-cache keys don't include the database path; drop cached
            # results so a re-pointed instance can't serve stale rows.
            try:
                with inst._query_cache_lock:
                    inst._query_cache.clear()
            except Exception:
                pass
            cls._instance = None

    def fetch_last_insert_id(self) -> Optional[int]:
        """Get the last insert ID (compatibility shim for migration tracker).

        Returns the rowid of the last successful INSERT.
        For SQLite, the rowid captured on the cursor at INSERT time inside
        ``execute()`` is preferred: SQLite's ``last_insert_rowid()`` is
        PER-CONNECTION, and thread-local connections are kept alive across
        requests, so any interleaved INSERT between this caller's INSERT and
        this lookup would previously return the WRONG rowid. Falls back to
        the SQL lookup only when no captured value exists. For PostgreSQL,
        uses LASTVAL().
        """
        if self.type != "postgres":
            stored = getattr(self._local, "last_insert_rowid", None)
            if stored is not None:
                return stored

        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            if self.type == "postgres":
                cursor.execute("SELECT LASTVAL()")
            else:
                cursor.execute("SELECT last_insert_rowid()")
            result = cursor.fetchone()
            if result:
                return result[0] if not hasattr(result, "keys") else result[0]
            return None
        finally:
            cursor.close()

    def table(self, table_name: str):
        """Get a TableQuery builder for the given table.

        Args:
            table_name: Name of the table

        Returns:
            TableQuery instance for method chaining (insert, select, update, delete)
        """
        conn = self._get_conn()
        qb = QueryBuilder(conn, self.type, database=self)
        return qb.table(table_name)

    @property
    def transaction_depth(self) -> int:
        if not hasattr(self._local, "transaction_depth"):
            self._local.transaction_depth = 0
        return self._local.transaction_depth

    @transaction_depth.setter
    def transaction_depth(self, value: int):
        self._local.transaction_depth = value

    @property
    def in_transaction(self) -> bool:
        if not hasattr(self._local, "in_transaction"):
            self._local.in_transaction = False
        return self._local.in_transaction

    @in_transaction.setter
    def in_transaction(self, value: bool):
        self._local.in_transaction = value
        self._in_transaction = value

    @property
    def connection(self) -> Optional[DbConnection]:
        if hasattr(self._local, "connection"):
            return self._local.connection
        return None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False
