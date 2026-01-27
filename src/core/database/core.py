"""
Database module - Provides database connectivity for SQLite and PostgreSQL.

This module follows the zero-friction pattern established by common-utils.
It acts as a facade, delegating to engine-specific, monitoring, and dialect components.
"""

import sqlite3
import threading
import time
import uuid
from typing import Any, List, Optional, Tuple, Union, Dict

import utils.config as config
import utils.logger as logger

from .engines.sqlite import SqliteEngine
from .engines.postgres import PostgresEngine
from .monitoring import DatabaseMonitor
from . import dialect

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
    """Thread-local storage for database connections and state."""
    pass

class Database:
    """
    Database connection manager supporting SQLite and PostgreSQL.
    Ensures thread-safety using thread-local storage.
    Acts as a facade for modular database components.
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
        
        # Initialize components
        if self.type == "postgres":
            self.engine = PostgresEngine(self.config)
            # Pre-initialize pool for Postgres
            pool_config = self.config.get("connection_pool", {})
            min_conn = pool_config.get("min_connections", 1)
            max_conn = pool_config.get("max_connections", 50) # Match original max
            try:
                self._pool = self.engine.create_pool(min_conn, max_conn)
                logger.info(f"PostgreSQL connection pool initialized: {min_conn}-{max_conn} connections")
            except Exception as e:
                logger.error(f"Failed to initialize PostgreSQL pool: {e}")
        elif self.type == "sqlite":
            self.engine = SqliteEngine(self.config)
        else:
            self.engine = None

        self.monitor = DatabaseMonitor(self.config, self.type)
        
        # Alert thresholds
        monitoring_config = self.config.get("monitoring", {})
        alert_thresholds = monitoring_config.get("alert_thresholds", {})
        self._slow_query_threshold_ms = alert_thresholds.get("query_time_ms", 5000)
        
        # Connection pool validation configuration
        pool_config = self.config.get("connection_pool", {})
        self._enable_validation = pool_config.get("enable_validation", True)
        self._validation_query = pool_config.get("validation_query", "SELECT 1")
        self._validation_interval = pool_config.get("validation_interval", 60)
        self._max_idle_time = pool_config.get("max_idle_time", 300)
        
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
        return False

    def get_pool_stats(self) -> Dict[str, Any]:
        """Return current pool utilization statistics."""
        engine_stats = self.engine.get_pool_stats(self._pool)
        return self.monitor.get_pool_stats(engine_stats)

    def _get_conn(self, auto_connect: bool = True) -> DbConnection:
        """Get or create a thread-local database connection with validation."""
        if self.get_correlation_id() is None:
            self.set_correlation_id(self._generate_correlation_id())
        
        if hasattr(self._local, "connection") and self._local.connection is not None:
            conn = self._local.connection
            is_valid = True
            conn_id = id(conn)
            
            # Validation logic
            if self.type == "postgres":
                if hasattr(conn, "closed") and conn.closed != 0:
                    is_valid = False
            
            if is_valid:
                metadata = self.monitor.get_connection_metadata(conn_id)
                if metadata:
                    # Max idle time check
                    last_used = metadata.get("last_used", time.time())
                    if self._max_idle_time > 0 and (time.time() - last_used) > self._max_idle_time:
                        logger.warning(f"Connection {conn_id} exceeded max idle time, evicting")
                        is_valid = False
                    
                    # Periodic validation query
                    elif self._enable_validation:
                        last_val = metadata.get("last_validation", 0)
                        if (time.time() - last_val) >= self._validation_interval:
                            try:
                                with conn.cursor() as cursor:
                                    cursor.execute(self._validation_query)
                                metadata["last_validation"] = time.time()
                            except Exception as e:
                                logger.warning(f"Connection {conn_id} failed validation: {e}")
                                is_valid = False
            
            if is_valid:
                self.monitor.update_connection_last_used(conn_id)
                return conn
            else:
                logger.warning("Thread-local connection invalid, reconnecting")
                self.engine.close_connection(conn, self._pool, {"close": True})
                self._local.connection = None

        if not auto_connect:
            raise ConnectionError("Database not connected. Call connect() first.")

        self.connect()
        return self._local.connection

    def connect(self) -> Optional[DbConnection]:
        """Establish a connection for the current thread."""
        if self.type not in ["sqlite", "postgres"]:
            raise ValueError(f"Unsupported database type: {self.type}")

        with self._lock:
            # Close existing if any
            if hasattr(self._local, "connection") and self._local.connection:
                self.engine.close_connection(self._local.connection, self._pool)
                self._local.connection = None
            
            start_time = time.time()
            if self.type == "postgres":
                if not self._pool:
                    pool_config = self.config.get("connection_pool", {})
                    self._pool = self.engine.create_pool(
                        pool_config.get("min_connections", 1),
                        pool_config.get("max_connections", 50)
                    )
                conn = self.engine.connect(self._pool)
            else:
                conn = self.engine.connect()
            
            duration = time.time() - start_time
            self.monitor.record_acquisition(duration)
            
            self._local.connection = conn
            self.transaction_depth = 0
            self.in_transaction = False
            
            conn_id = id(conn)
            self.monitor.add_connection_metadata(conn_id, {
                "created_at": time.time(),
                "last_used": time.time(),
                "thread_id": threading.current_thread().ident,
                "acquired_at": time.time()
            })
            
            return conn

    def execute(self, query: str, params: Optional[Tuple] = None) -> DbCursor:
        """Execute a query and return a cursor."""
        if self.transaction_depth > 0:
            self._validate_transaction_state()
            
        conn = self._get_conn()
        query = dialect.convert_placeholders(query, self.type)
        
        cursor = conn.cursor()
        start_time = time.time()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            duration_ms = (time.time() - start_time) * 1000
            self.monitor.record_query_execution(duration_ms)
            
            if duration_ms > self._slow_query_threshold_ms:
                logger.warning(f"Slow query detected ({duration_ms:.2f}ms): {query[:100]}")
            
            if not self.in_transaction:
                conn.commit()
                
            return cursor
        except Exception as e:
            self.monitor.record_error(type(e).__name__)
            self._handle_execution_error(conn, cursor, e, query)
            raise

    def execute_many(self, query: str, params_list: List[Tuple]) -> DbCursor:
        """Execute a batch query."""
        if self.transaction_depth > 0:
            self._validate_transaction_state()
            
        conn = self._get_conn()
        query = dialect.convert_placeholders(query, self.type)
        
        cursor = conn.cursor()
        start_time = time.time()
        try:
            cursor.executemany(query, params_list)
            exec_time = (time.time() - start_time) * 1000
            self.monitor.record_query_execution(exec_time)
            
            if not self.in_transaction:
                conn.commit()
            return cursor
        except Exception as e:
            self.monitor.record_error(type(e).__name__)
            self._handle_execution_error(conn, cursor, e, query)
            raise

    def fetch_one(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict[str, Any]]:
        """Fetch a single result as a dict."""
        cursor = self.execute(query, params)
        result = cursor.fetchone()
        cursor.close()
        return dict(result) if result else None

    def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        """Fetch all results as a list of dicts."""
        cursor = self.execute(query, params)
        results = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in results]

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
                conn.execute("BEGIN")
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
                logger.warning(f"Savepoint release failed: {e}, rolling back entire transaction")
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
                logger.warning(f"Savepoint rollback failed: {e}, rolling back entire transaction")
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

    def upsert(self, table: str, columns: List[str], values: Tuple, conflict_columns: List[str], update_columns: Optional[List[str]] = None) -> None:
        """Secure cross-database UPSERT."""
        safe_table = dialect.sanitize_identifier(table, self.type)
        safe_cols = [dialect.sanitize_identifier(c, self.type) for c in columns]
        safe_conflict = [dialect.sanitize_identifier(c, self.type) for c in conflict_columns]
        
        if update_columns is None:
            update_columns = [c for c in columns if c not in conflict_columns]
        safe_updates = [dialect.sanitize_identifier(c, self.type) for c in update_columns]

        if not safe_updates:
            self.insert_or_ignore(table, columns, values)
            return

        query = self.engine.get_upsert_query(safe_table, safe_cols, safe_conflict, safe_updates)
        cursor = self.execute(query, values)
        cursor.close()

    def close(self) -> None:
        """Close thread-local connection."""
        self.stop_pool_monitoring()
        if hasattr(self._local, "connection") and self._local.connection:
            conn = self._local.connection
            conn_id = id(conn)
            self.monitor.check_connection_age(conn_id)
            self.engine.close_connection(conn, self._pool)
            self.monitor.remove_connection_metadata(conn_id)
            self._local.connection = None
        
        self.transaction_depth = 0
        self.in_transaction = False

    def start_pool_monitoring(self):
        """Start periodic pool monitoring."""
        self.monitor.start_pool_monitoring(self.get_pool_stats)

    def stop_pool_monitoring(self):
        """Stop periodic pool monitoring."""
        self.monitor.stop_pool_monitoring()

    def get_correlation_id(self) -> Optional[str]:
        """Get correlation ID for current thread."""
        return getattr(self._local, "correlation_id", None)

    def set_correlation_id(self, correlation_id: str):
        """Set correlation ID for current thread."""
        self._local.correlation_id = correlation_id

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
                    "InFailedSqlTransaction" in type(e).__name__ or
                    pg_code == "25P02" or
                    "current transaction is aborted" in err_msg.lower()
                )
                
                if is_failed:
                    logger.warning(f"Detected failed transaction state ({err_msg}), recovering...")
                    try:
                        conn.rollback()
                        logger.info("Successfully recovered from failed transaction state")
                    except Exception as rb_e:
                        logger.error(f"Failed to recover from transaction error: {rb_e}")
                    finally:
                        self.transaction_depth = 0
                        self.in_transaction = False

    def _handle_execution_error(self, conn: DbConnection, cursor: DbCursor, e: Exception, query: str):
        """Handle errors during query execution."""
        error_type = type(e).__name__
        logger.error(f"Query failed ({error_type}): {str(e)[:100]}")
        
        try:
            if self.type == "postgres":
                if hasattr(conn, "closed") and conn.closed > 0:
                    self.transaction_depth = 0
                    self.in_transaction = False
                elif self.transaction_depth > 0:
                    try:
                        cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}")
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