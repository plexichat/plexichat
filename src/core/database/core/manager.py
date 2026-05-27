"""
Database module - Provides database connectivity for SQLite and PostgreSQL.

This module follows the zero-friction pattern established by common-utils.
It acts as a facade, delegating to engine-specific, monitoring, and dialect components.
"""

import threading
from typing import Any, Dict, Optional, Tuple

import utils.config as config
import utils.logger as logger

from ..engines.base import BaseEngine
from ..engines.sqlite import SqliteEngine
from ..engines.postgres import PostgresEngine
from ..monitoring import DatabaseMonitor
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
    def __init__(self):
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

        self._query_cache: Dict[str, Tuple[float, Any]] = {}
        self._query_cache_ttl = 0.0
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
        if self.type == "postgres":
            self.start_pool_monitoring()

    def fetch_last_insert_id(self) -> Optional[int]:
        """Get the last insert ID (compatibility shim for migration tracker).

        Returns the rowid of the last successful INSERT.
        For SQLite, uses last_insert_rowid(). For PostgreSQL, uses LASTVAL().
        """
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
