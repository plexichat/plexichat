"""
Database module - Provides database connectivity for SQLite and PostgreSQL.

This module follows the zero-friction pattern established by common-utils.
It acts as a facade, delegating to engine-specific, monitoring, and dialect components.
"""

import sqlite3
import threading
import time
import uuid
import inspect
from functools import wraps
from typing import Any, List, Optional, Tuple, Union, Dict
from contextvars import ContextVar

import utils.config as config
import utils.logger as logger

from ..engines.base import BaseEngine
from ..engines.sqlite import SqliteEngine
from ..engines.postgres import PostgresEngine
from ..monitoring import DatabaseMonitor
from .. import dialect

# Context variables for request-local database metrics
_query_count: ContextVar[int] = ContextVar("_query_count", default=0)
_query_time_ms: ContextVar[float] = ContextVar("_query_time_ms", default=0.0)


# Export for backward compatibility with tests
class _CompatibilityRegex:
    def __init__(self, pattern):
        self.pattern = pattern

    def sub(self, repl, string):
        def wrapper(match):
            if match.group(1):
                return match.group(1)
            return repl

        return self.pattern.sub(wrapper, string)

    def __getattr__(self, name):
        return getattr(self.pattern, name)


_PLACEHOLDER_PATTERN = _CompatibilityRegex(dialect._PLACEHOLDER_PATTERN)

# Type alias for database connections
DbConnection = Union[sqlite3.Connection, Any]  # Any for psycopg2 connection
DbCursor = Union[sqlite3.Cursor, Any]  # Any for psycopg2 cursor


class DatabaseLocal(threading.local):
    """Thread-local storage for database connections."""

    def __init__(self):
        pass


class Database:
    """
    Database connection manager supporting SQLite and PostgreSQL.
    Ensures thread-safety using thread-local storage for connections.
    Uses ContextVars for request-scoped metrics.
    """

    def __init__(self):
        """Initialize the database manager with configuration."""
        db_config = config.get("database")

        if db_config is None:
            logger.warning("Database configuration not found. Using default SQLite.")
            db_config = {"type": "sqlite", "path": "data/database.db"}
        elif not isinstance(db_config, dict):
            logger.error("Database configuration is malformed. Using default SQLite.")
            db_config = {"type": "sqlite", "path": "data/database.db"}

        self.config: Dict[str, Any] = db_config
        self.type: str = self.config.get("type", "sqlite")

        self._pool = None  # PostgreSQL connection pool
        self._local = DatabaseLocal()
        self._lock = threading.RLock()

        # Local cache for redundant queries within the same request context
        self._query_cache: Dict[str, Tuple[float, Any]] = {}
        self._query_cache_ttl = 1.0  # 1 second TTL for identical queries
        self._query_cache_lock = threading.RLock()

        # Initialize components
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

        # Alert thresholds
        monitoring_config = config.get("monitoring", {})
        alert_thresholds = monitoring_config.get("alert_thresholds", {})
        self._slow_query_threshold_ms = alert_thresholds.get("query_time_ms", 5000)

        # Connection pool validation configuration
        pool_config = self.config.get("connection_pool", {})
        self._enable_validation = pool_config.get("enable_validation", True)
        self._validation_query = pool_config.get("validation_query", "SELECT 1")
        self._validation_interval = pool_config.get("validation_interval", 60)
        self._max_idle_time = pool_config.get("max_idle_time", 3600)

        # Max age threshold (default 2 hours)
        max_age_hours = pool_config.get("max_connection_age_hours", 2.0)
        self._max_connection_age_seconds = max_age_hours * 3600

        logger.info(f"Database initialized with type: {self.type}")
        self.start_pool_monitoring()

    @property
    def transaction_depth(self) -> int:
        """Get the current thread's transaction depth."""
        if not hasattr(self._local, "transaction_depth"):
            self._local.transaction_depth = 0
        return self._local.transaction_depth

    @transaction_depth.setter
    def transaction_depth(self, value: int):
        """Set the current thread's transaction depth."""
        self._local.transaction_depth = value

    @property
    def in_transaction(self) -> bool:
        """Get the current thread's transaction status."""
        if not hasattr(self._local, "in_transaction"):
            self._local.in_transaction = False
        return self._local.in_transaction

    @in_transaction.setter
    def in_transaction(self, value: bool):
        """Set the current thread's transaction status."""
        self._local.in_transaction = value

    @property
    def connection(self) -> Optional[DbConnection]:
        """Get the current thread-local connection."""
        if hasattr(self._local, "connection"):
            return self._local.connection
        return None

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False

    def get_pool_stats(self) -> Dict[str, Any]:
        """Return current pool utilization statistics."""
        engine_stats = self.engine.get_pool_stats(self._pool)
        return self.monitor.get_pool_stats(engine_stats)

    def _get_conn(self, auto_connect: bool = True) -> DbConnection:
        """Get or create a thread-local database connection with validation."""
        assert self.engine is not None, "Database engine not initialized"
        if self.get_correlation_id() is None:
            self.set_correlation_id(self._generate_correlation_id())

        if hasattr(self._local, "connection") and self._local.connection is not None:
            conn = self._local.connection
            is_valid = True
            conn_id = id(conn)

            # Optimization: Cache validity status for a few seconds to reduce overhead
            now = time.time()
            last_valid_check = getattr(self._local, "last_valid_check", 0)
            if now - last_valid_check < 5.0:
                return conn

            # Validation logic
            if self.type == "postgres":
                # Check if connection object says it's closed
                if getattr(conn, "closed", 0) != 0:
                    is_valid = False

                # Proactive check for network/server-side closure
                if is_valid and hasattr(conn, "poll"):
                    PsycopgOperationalError = Exception
                    try:
                        from psycopg2 import OperationalError as PsycopgOperationalError  # type: ignore
                    except Exception:
                        pass
                    try:
                        conn.poll()
                    except PsycopgOperationalError as e:
                        logger.debug(f"Connection {conn_id} failed poll check: {e}")
                        is_valid = False
                    except Exception as e:
                        logger.debug(f"Connection {conn_id} failed poll check: {e}")
                        is_valid = False

            if is_valid:
                metadata = self.monitor.get_connection_metadata(conn_id)
                if metadata:
                    # Max idle time check
                    last_used = metadata.get("last_used", now)
                    if (
                        self._max_idle_time > 0
                        and (now - last_used) > self._max_idle_time
                    ):
                        logger.warning(
                            f"Connection {conn_id} exceeded max idle time, evicting"
                        )
                        is_valid = False

                    # Max age check
                    elif self._max_connection_age_seconds > 0:
                        created_at = metadata.get("created_at", 0)
                        if (now - created_at) > self._max_connection_age_seconds:
                            logger.warning(
                                f"Connection {conn_id} exceeded max age ({now - created_at:.1f}s), evicting"
                            )
                            is_valid = False

                    # Periodic validation query
                    if is_valid and self._enable_validation:
                        last_val = metadata.get("last_validation", 0)
                        if (now - last_val) >= self._validation_interval:
                            try:
                                cursor = conn.cursor()
                                cursor.execute(self._validation_query)
                                cursor.close()
                                metadata["last_validation"] = now
                            except Exception as e:
                                logger.warning(
                                    f"Connection {conn_id} failed validation: {e}"
                                )
                                is_valid = False

            if is_valid:
                self._local.last_valid_check = now
                self.monitor.update_connection_last_used(conn_id)
                return conn
            else:
                logger.warning(
                    f"Thread-local connection {conn_id} invalid, reconnecting"
                )
                try:
                    self.engine.close_connection(conn, self._pool, {"close": True})
                except Exception:
                    pass
                self._local.connection = None
                self._local.last_valid_check = 0
                self._local.last_validation = 0

        if not auto_connect:
            raise ConnectionError("Database not connected. Call connect() first.")

        self.connect()
        return self._local.connection

    def connect(self) -> Optional[DbConnection]:
        """Establish a connection for the current thread."""
        with self._lock:
            # Close existing if any
            if hasattr(self._local, "connection") and self._local.connection:
                try:
                    self.engine.close_connection(
                        self._local.connection, self._pool, {"close": True}
                    )
                except Exception:
                    pass
                self._local.connection = None

            start_time = time.time()
            if self.type == "postgres" and isinstance(self.engine, PostgresEngine):
                if not self._pool:
                    pool_config = self.config.get("connection_pool", {})
                    min_conn = pool_config.get("min_connections", 20)
                    self._pool = self.engine.create_pool(
                        min_conn, pool_config.get("max_connections", 100)
                    )
                conn = self.engine.connect(self._pool)
            else:
                conn = self.engine.connect()

            duration = time.time() - start_time
            self.monitor.record_acquisition(duration)

            self._local.connection = conn
            self._local.last_valid_check = time.time()
            self.transaction_depth = 0
            self.in_transaction = False

            conn_id = id(conn)
            self.monitor.add_connection_metadata(
                conn_id,
                {
                    "connection": conn,
                    "created_at": time.time(),
                    "last_used": time.time(),
                    "thread_id": threading.current_thread().ident,
                    "acquired_at": time.time(),
                },
            )

            return conn

    def execute(
        self, query: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> DbCursor:
        """Execute a query and return a cursor with multi-retry on connection failure."""
        if self.transaction_depth > 0:
            self._validate_transaction_state()

        # Invalidate cache on any potentially modifying operation
        upper_query = query.strip().upper()
        if any(
            upper_query.startswith(word)
            for word in [
                "INSERT",
                "UPDATE",
                "DELETE",
                "REPLACE",
                "DROP",
                "CREATE",
                "ALTER",
            ]
        ):
            with self._query_cache_lock:
                self._query_cache.clear()

        query_conv = dialect.convert_placeholders(query, self.type)

        # Max 3 attempts (initial + 2 retries)
        for attempt in range(3):
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                start_time = time.time()

                if params:
                    cursor.execute(query_conv, params)
                else:
                    cursor.execute(query_conv)

                duration_ms = (time.time() - start_time) * 1000
                self.monitor.record_query_execution(duration_ms)

                # Update request context metrics using ContextVars
                _query_count.set(_query_count.get() + 1)
                _query_time_ms.set(_query_time_ms.get() + duration_ms)

                if duration_ms > self._slow_query_threshold_ms:
                    logger.warning(
                        f"Slow query detected ({duration_ms:.2f}ms): {query[:100]}"
                    )

                if auto_commit and not self.in_transaction:
                    conn.commit()

                return cursor
            except Exception as e:
                # Check for connection-related errors that warrant a retry
                is_conn_error = (
                    "OperationalError" in type(e).__name__ or "closed" in str(e).lower()
                )

                if attempt < 2 and is_conn_error and not self.in_transaction:
                    logger.warning(
                        f"Database connection error (attempt {attempt + 1}), retrying: {e}"
                    )
                    # Force a new connection on next attempt
                    if hasattr(self._local, "connection") and self._local.connection:
                        try:
                            self.engine.close_connection(
                                self._local.connection, self._pool, {"close": True}
                            )
                        except Exception:
                            pass
                        self._local.connection = None
                    continue

                # If retry failed or not a retryable error, handle and re-raise
                self.monitor.record_error(type(e).__name__)
                temp_conn = getattr(self._local, "connection", None)
                self._handle_execution_error(
                    temp_conn, locals().get("cursor"), e, query
                )
                raise

    def execute_many(
        self, query: str, params_list: List[Tuple], auto_commit: bool = True
    ) -> DbCursor:
        """Execute a batch query with multi-retry on connection failure."""
        if self.transaction_depth > 0:
            self._validate_transaction_state()

        query_conv = dialect.convert_placeholders(query, self.type)

        # Max 3 attempts
        for attempt in range(3):
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                start_time = time.time()

                cursor.executemany(query_conv, params_list)
                exec_time = (time.time() - start_time) * 1000
                self.monitor.record_query_execution(exec_time)

                # Update request context metrics using ContextVars
                _query_count.set(_query_count.get() + 1)
                _query_time_ms.set(_query_time_ms.get() + exec_time)

                if auto_commit and not self.in_transaction:
                    conn.commit()
                return cursor
            except Exception as e:
                is_conn_error = (
                    "OperationalError" in type(e).__name__ or "closed" in str(e).lower()
                )

                if attempt < 2 and is_conn_error and not self.in_transaction:
                    logger.warning(
                        f"Database connection error during execute_many (attempt {attempt + 1}), retrying: {e}"
                    )
                    if hasattr(self._local, "connection") and self._local.connection:
                        try:
                            self.engine.close_connection(
                                self._local.connection, self._pool, {"close": True}
                            )
                        except Exception:
                            pass
                        self._local.connection = None
                    continue

                self.monitor.record_error(type(e).__name__)
                temp_conn = getattr(self._local, "connection", None)
                self._handle_execution_error(
                    temp_conn, locals().get("cursor"), e, query
                )
                raise

    def fetch_one(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single result as a dict with local caching."""
        cache_key = f"one:{query}:{params}"
        now = time.time()

        # Check local cache
        with self._query_cache_lock:
            if cache_key in self._query_cache:
                expiry, result = self._query_cache[cache_key]
                if now < expiry:
                    return result
                del self._query_cache[cache_key]

        cursor = self.execute(query, params)
        result = cursor.fetchone()
        cursor.close()

        final_result = dict(result) if result else None

        # Store in local cache
        with self._query_cache_lock:
            self._query_cache[cache_key] = (now + self._query_cache_ttl, final_result)
            if len(self._query_cache) > 100:
                self._query_cache = {
                    k: v for k, v in self._query_cache.items() if v[0] > now
                }

        return final_result

    def fetch_all(
        self, query: str, params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]:
        """Fetch all results as a list of dicts with local caching."""
        cache_key = f"all:{query}:{params}"
        now = time.time()

        # Check local cache
        with self._query_cache_lock:
            if cache_key in self._query_cache:
                expiry, result = self._query_cache[cache_key]
                if now < expiry:
                    return result
                del self._query_cache[cache_key]

        cursor = self.execute(query, params)
        results = cursor.fetchall()
        cursor.close()

        final_results = [dict(row) for row in results]

        # Store in local cache
        with self._query_cache_lock:
            self._query_cache[cache_key] = (now + self._query_cache_ttl, final_results)

        return final_results

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        query, params = self.engine.get_table_exists_query(table_name)
        result = self.fetch_one(query, params)
        return result is not None

    def begin_transaction(self) -> None:
        """Begin a transaction with savepoint support."""
        conn = self._get_conn()
        if self.transaction_depth == 0:
            if self.type == "sqlite":
                # Use IMMEDIATE to acquire write lock upfront, preventing deadlocks
                conn.execute("BEGIN IMMEDIATE")
            self.in_transaction = True

        self.transaction_depth += 1
        cursor = conn.cursor()
        cursor.execute(f"SAVEPOINT sp_{self.transaction_depth}")
        cursor.close()

    def commit(self) -> None:
        """Commit the current transaction level."""
        conn = self._get_conn()
        if self.transaction_depth > 0:
            cursor = conn.cursor()
            try:
                cursor.execute(f"RELEASE SAVEPOINT sp_{self.transaction_depth}")
                cursor.close()
            except Exception as e:
                logger.warning(
                    f"Savepoint release failed: {e}, rolling back entire transaction"
                )
                cursor.close()
                conn.rollback()
                self.transaction_depth = 0
                self.in_transaction = False
                raise

            self.transaction_depth -= 1
            if self.transaction_depth == 0:
                conn.commit()
                self.in_transaction = False
        else:
            conn.commit()

    def rollback(self) -> None:
        """Rollback the current transaction level."""
        conn = self._get_conn()
        if self.transaction_depth > 0:
            cursor = conn.cursor()
            try:
                cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}")
                cursor.close()
            except Exception as e:
                logger.warning(
                    f"Savepoint rollback failed: {e}, rolling back entire transaction"
                )
                cursor.close()
                conn.rollback()
                self.transaction_depth = 0
                self.in_transaction = False
                return

            self.transaction_depth -= 1
            if self.transaction_depth == 0:
                conn.rollback()
                self.in_transaction = False
        else:
            conn.rollback()
            self.in_transaction = False

    def insert_or_ignore(self, table: str, columns: List[str], values: Tuple) -> bool:
        """Secure cross-database INSERT OR IGNORE."""
        safe_table = dialect.sanitize_identifier(table, self.type)
        safe_cols = [dialect.sanitize_identifier(c, self.type) for c in columns]
        query = self.engine.get_insert_or_ignore_query(safe_table, safe_cols)
        cursor = self.execute(query, values)
        count = cursor.rowcount
        cursor.close()
        return count > 0

    def upsert(
        self,
        table: str,
        columns: List[str],
        values: Tuple,
        conflict_columns: List[str],
        update_columns: Optional[List[str]] = None,
    ) -> None:
        """Secure cross-database UPSERT."""
        safe_table = dialect.sanitize_identifier(table, self.type)
        safe_cols = [dialect.sanitize_identifier(c, self.type) for c in columns]
        safe_conflict = [
            dialect.sanitize_identifier(c, self.type) for c in conflict_columns
        ]

        if update_columns is None:
            update_columns = [c for c in columns if c not in conflict_columns]
        safe_updates = [
            dialect.sanitize_identifier(c, self.type) for c in update_columns
        ]

        if not safe_updates:
            self.insert_or_ignore(table, columns, values)
            return

        query = self.engine.get_upsert_query(
            safe_table, safe_cols, safe_conflict, safe_updates
        )
        cursor = self.execute(query, values)
        cursor.close()

    def close_connection(
        self,
        conn: DbConnection,
        pool: Optional[Any] = None,
        params: Optional[Dict] = None,
    ) -> None:
        """Close a database connection."""
        if self.engine is None:
            return
        self.engine.close_connection(conn, pool, params)

    def close(self) -> None:
        """Close thread-local connection and return to pool."""
        # Note: we don't stop pool monitoring here as it's global,
        # only the connection is thread-local.

        conn = None
        if hasattr(self._local, "connection"):
            conn = self._local.connection
            self._local.connection = None

        if conn:
            conn_id = id(conn)

            # Safety: rollback any dangling transaction
            # Check if connection is still open before rollback
            is_closed = False
            if self.type == "postgres":
                is_closed = getattr(conn, "closed", 0) != 0

            if not is_closed and (
                getattr(self._local, "in_transaction", False)
                or getattr(self._local, "transaction_depth", 0) > 0
            ):
                try:
                    conn.rollback()
                except Exception:
                    pass

            # Check age to decide if we should close or return to pool
            force_close = False
            metadata = self.monitor.get_connection_metadata(conn_id)
            if metadata and self._max_connection_age_seconds > 0:
                age = time.time() - metadata.get("created_at", 0)
                if age > self._max_connection_age_seconds:
                    logger.info(
                        f"Closing connection {conn_id} on thread exit (age: {age:.1f}s > {self._max_connection_age_seconds}s)"
                    )
                    force_close = True

            try:
                self.engine.close_connection(conn, self._pool, {"close": force_close})
            except Exception as e:
                logger.error(f"Error returning connection {conn_id} to pool: {e}")
            finally:
                self.monitor.remove_connection_metadata(conn_id)

        # Reset all thread-local state
        self._local.transaction_depth = 0
        self._local.in_transaction = False
        self._local.correlation_id = None

        # Backward compatibility for any external access
        self.transaction_depth = 0
        self.in_transaction = False

    def start_pool_monitoring(self):
        """Start periodic pool monitoring."""
        self.monitor.start_pool_monitoring(self.get_pool_stats, self.reap_connections)

    def reap_connections(self) -> int:
        """
        Proactively close connections that are idle or leaked in other threads.
        Returns the number of connections reaped.
        """
        if self.type != "postgres" or not self._pool:
            return 0

        reaped_count = 0
        current_time = time.time()

        # We need a list of IDs to avoid dictionary mutation during iteration
        connection_ids = list(self.monitor._connection_metadata.keys())

        for conn_id in connection_ids:
            metadata = self.monitor.get_connection_metadata(conn_id)
            if not metadata or "connection" not in metadata:
                continue

            last_used = metadata.get("last_used", 0)
            idle_time = current_time - last_used

            # If it's been idle longer than our threshold, and it's NOT the current thread's connection
            is_current_thread_conn = (
                hasattr(self._local, "connection")
                and id(self._local.connection) == conn_id
            )

            if idle_time > self._max_idle_time and not is_current_thread_conn:
                conn = metadata["connection"]
                logger.warning(
                    f"Reaping leaked/idle connection {conn_id} (idle {idle_time:.1f}s)"
                )
                try:
                    self.engine.close_connection(conn, self._pool, {"close": True})
                    reaped_count += 1
                except Exception as e:
                    logger.error(f"Error reaping connection {conn_id}: {e}")
                finally:
                    self.monitor.remove_connection_metadata(conn_id)

        return reaped_count

    def stop_pool_monitoring(self):
        """Stop periodic pool monitoring."""
        self.monitor.stop_pool_monitoring()

    def get_correlation_id(self) -> Optional[str]:
        """Get correlation ID for current thread."""
        return getattr(self._local, "correlation_id", None)

    def set_correlation_id(self, correlation_id: str):
        """Set correlation ID for current thread."""
        self._local.correlation_id = correlation_id

    def get_request_metrics(self) -> Dict[str, Union[int, float]]:
        """Get database metrics for the current request context."""
        return {
            "query_count": _query_count.get(),
            "query_time_ms": _query_time_ms.get(),
        }

    def reset_request_metrics(self):
        """Reset database metrics for the current request context."""
        _query_count.set(0)
        _query_time_ms.set(0.0)

    def _generate_correlation_id(self) -> str:
        """Generate a unique correlation ID."""
        return str(uuid.uuid4())

    def _validate_transaction_state(self):
        """Ensure transaction state is healthy."""
        if self.type == "postgres" and hasattr(self._local, "connection"):
            conn = self._local.connection
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except Exception as e:
                # Check for InFailedSqlTransaction more robustly
                err_msg = str(e)
                pg_code = getattr(e, "pgcode", None)

                is_failed = (
                    "InFailedSqlTransaction" in type(e).__name__
                    or pg_code == "25P02"
                    or "current transaction is aborted" in err_msg.lower()
                )

                if is_failed:
                    logger.warning(
                        f"Detected failed transaction state ({err_msg}), recovering..."
                    )
                    try:
                        conn.rollback()
                        logger.info(
                            "Successfully recovered from failed transaction state"
                        )
                    except Exception as rb_e:
                        logger.error(
                            f"Failed to recover from transaction error: {rb_e}"
                        )
                    finally:
                        self.transaction_depth = 0
                        self.in_transaction = False

    def _handle_execution_error(
        self, conn: DbConnection, cursor: DbCursor, e: Exception, query: str
    ):
        """Handle errors during query execution."""
        error_type = type(e).__name__
        logger.error(f"Query failed ({error_type}): {str(e)[:100]}")

        try:
            if self.type == "postgres":
                if getattr(conn, "closed", 0) > 0:
                    self.transaction_depth = 0
                    self.in_transaction = False
                elif self.transaction_depth > 0:
                    try:
                        cursor.execute(
                            f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}"
                        )
                    except Exception:
                        conn.rollback()
                        self.transaction_depth = 0
                        self.in_transaction = False
                else:
                    conn.rollback()
                    self.in_transaction = False
            elif not self.in_transaction:
                conn.rollback()
        except Exception as cleanup_e:
            logger.error(f"Error during error cleanup: {cleanup_e}")
        finally:
            cursor.close()

    def convert_schema(self, schema: str) -> str:
        """Safer schema conversion for Postgres."""
        if self.type != "postgres":
            return schema

        import re

        converted = schema
        # BLOB -> BYTEA
        converted = re.sub(r"\bBLOB\b", "BYTEA", converted, flags=re.IGNORECASE)
        # Snowflake IDs: INTEGER -> BIGINT
        converted = re.sub(r"\bINTEGER\b", "BIGINT", converted, flags=re.IGNORECASE)

        return converted

    def _sanitize_identifier(self, identifier: str) -> str:
        """Backward compatibility for private method."""
        return dialect.sanitize_identifier(identifier, self.type)

    def _convert_placeholders(self, query: str) -> str:
        """Backward compatibility for private method."""
        return dialect.convert_placeholders(query, self.type)

    def _check_connection_age(self, conn_id: int):
        """Backward compatibility for private method."""
        self.monitor.check_connection_age(conn_id)

    def _format_log_context(self, **kwargs) -> str:
        """Backward compatibility for private method."""
        ctx = [f"{k}={v}" for k, v in kwargs.items()]
        correlation_id = self.get_correlation_id()
        if correlation_id:
            ctx.append(f"correlation_id={correlation_id}")
        return f"[{' '.join(ctx)}]"


def with_db_worker(func):
    """
    Decorator for synchronous functions run in run_in_threadpool
    that ensures the database connection is closed after execution.
    """
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            import src.api as api

            db = api.get_db()
            try:
                return await func(*args, **kwargs)
            finally:
                if db:
                    db.close()

        return async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            import src.api as api

            db = api.get_db()
            try:
                return func(*args, **kwargs)
            finally:
                if db:
                    db.close()

        return sync_wrapper
