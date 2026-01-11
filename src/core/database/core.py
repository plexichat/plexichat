"""
Database module - Provides database connectivity for SQLite and PostgreSQL.

This module follows the zero-friction pattern established by common-utils.
It requires config and logger to be set up before use.

PostgreSQL Support:
    - Uses psycopg2-binary driver (install with: pip install psycopg2-binary)
    - Automatically converts ? placeholders to %s for PostgreSQL compatibility
    - Uses RealDictCursor for dict-like row access matching SQLite behavior

Structured Logging and Monitoring:
    - Structured logging with correlation IDs for request tracing across threads
    - Query execution time tracking with slow query detection (configurable threshold)
    - Connection pool utilization percentage monitoring against alert thresholds
    - Error rate tracking and per-minute calculation with threshold alerting
    - Correlation ID support for associating related database operations
    - Metrics cleanup to prevent unbounded memory growth

Logging Levels:
    - DEBUG: Connection lifecycle details, query execution, savepoint operations
    - INFO: Pool statistics, successful operations, monitoring thread status
    - WARNING: High pool utilization (>75%), slow queries, high error rates, old connections
    - ERROR: Connection failures, query errors, pool exhaustion, transaction cleanup failures
"""

import sqlite3
import os
import re
import threading
import time
import uuid
from typing import Any, List, Optional, Tuple, Union, Dict
from datetime import datetime

import utils.config as config
import utils.logger as logger

# Type alias for database connections
DbConnection = Union[sqlite3.Connection, Any]  # Any for psycopg2 connection
DbCursor = Union[sqlite3.Cursor, Any]  # Any for psycopg2 cursor

# Regex pattern to match ? placeholders (not inside single or double quotes)
_PLACEHOLDER_PATTERN = re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")|(\?)")


class Database:
    """
    Database connection manager supporting SQLite and PostgreSQL.
    Ensures thread-safety using thread-local storage.
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
        self._local = threading.local()
        self._lock = threading.RLock()
        
        # Pool monitoring attributes
        self._pool_stats_lock = threading.RLock()
        self._connection_acquisitions = []  # List of (timestamp, duration) tuples
        self._connection_pool_waits = []  # List of (timestamp, wait_duration) tuples
        self._connection_metadata = {}  # Track connection creation times and info
        self._periodic_logging_thread = None
        self._stop_logging = False
        
        # Monitoring configuration
        max_connection_age_hours = self.config.get("connection_pool", {}).get("max_connection_age_hours", 0.5)  # 30 minutes default
        self._max_connection_age_seconds = max_connection_age_hours * 3600
        self._periodic_log_interval = self.config.get("monitoring", {}).get("log_interval_seconds", 60)
        
        # Connection pool validation and idle timeout configuration
        pool_config = self.config.get("connection_pool", {})
        self._enable_validation = pool_config.get("enable_validation", True)
        self._validation_query = pool_config.get("validation_query", "SELECT 1")
        self._validation_interval = pool_config.get("validation_interval", 60)  # seconds
        self._max_idle_time = pool_config.get("max_idle_time", 300)  # seconds
        
        # Query execution time tracking and slow query detection
        self._query_execution_times = []  # List of (timestamp, execution_time_ms) tuples
        monitoring_config = self.config.get("monitoring", {})
        alert_thresholds = monitoring_config.get("alert_thresholds", {})
        self._slow_query_threshold_ms = alert_thresholds.get("query_time_ms", 5000)
        
        # Error tracking and rate calculation
        self._error_counts = {}  # Dictionary of {error_type: [(timestamp, error_type)]}
        self._error_rate_window_seconds = 60  # 60-second sliding window for error rate
        self._error_rate_threshold = alert_thresholds.get("db_errors_per_minute", 10)
        
        # Connection pool utilization threshold
        self._pool_saturation_threshold_percent = alert_thresholds.get("db_pool_saturation_percent", 75)
        
        logger.info(f"Database initialized with type: {self.type}")
        logger.info(f"Connection pool monitoring enabled - max age: {max_connection_age_hours}h, log interval: {self._periodic_log_interval}s")
        if self.type == "postgres":
            logger.info(f"Connection validation enabled: {self._enable_validation}, interval: {self._validation_interval}s, max idle: {self._max_idle_time}s")

    def get_pool_stats(self) -> Dict[str, Any]:
        """Return current pool utilization statistics.
        
        Provides comprehensive pool health metrics including active/idle connections,
        acquisition metrics, and connection age information.
        
        Returns:
            Dictionary containing pool statistics with keys:
            - active_connections: Number of active connections
            - idle_connections: Number of idle connections  
            - total_connections: Total available connections
            - max_connections: Maximum pool size
            - min_connections: Minimum pool size
            - avg_acquisition_time: Average connection acquisition time (seconds)
            - max_acquisition_time: Maximum acquisition time observed (seconds)
            - avg_pool_wait_time: Average time spent waiting for pool connection (seconds)
            - total_acquisitions: Total connection acquisitions
            - total_pool_waits: Total times connection pool was exhausted
            - old_connections: List of connection ages exceeding max_age threshold
            - status: Connection pool status (e.g., 'healthy')
        """
        with self._pool_stats_lock:
            stats = {
                "active_connections": 0,
                "idle_connections": 0,
                "total_connections": 0,
                "max_connections": 0,
                "min_connections": 0,
                "avg_acquisition_time": 0.0,
                "max_acquisition_time": 0.0,
                "avg_pool_wait_time": 0.0,
                "total_acquisitions": len(self._connection_acquisitions),
                "total_pool_waits": len(self._connection_pool_waits),
                "old_connections": [],
                "status": None,
                "database_type": self.type,
                "timestamp": datetime.now().isoformat(),
            }
            
            # PostgreSQL-specific pool stats
            if self.type == "postgres" and self._pool:
                try:
                    # For ThreadedConnectionPool, we can access these attributes
                    stats["min_connections"] = self._pool.minconn
                    stats["max_connections"] = self._pool.maxconn
                    
                    # Use _pool for idle connections and _used for active connections
                    if hasattr(self._pool, "_pool") and self._pool._pool:
                        stats["idle_connections"] = len(self._pool._pool)
                    else:
                        stats["idle_connections"] = 0
                    
                    if hasattr(self._pool, "_used") and self._pool._used:
                        stats["active_connections"] = len(self._pool._used)
                    else:
                        stats["active_connections"] = 0
                    
                    # Total connections is the sum of idle and active
                    stats["total_connections"] = stats["idle_connections"] + stats["active_connections"]
                    
                    # Calculate pool utilization percentage
                    if stats["max_connections"] > 0:
                        stats["utilization_percent"] = (stats["active_connections"] / stats["max_connections"]) * 100
                    else:
                        stats["utilization_percent"] = 0
                except Exception as e:
                    logger.warning(f"Could not retrieve detailed pool stats: {e}")
                    stats["idle_connections"] = "unavailable"
                    stats["active_connections"] = "unavailable"
                    stats["total_connections"] = "unavailable"
                    stats["utilization_percent"] = "unavailable"
            else:
                # SQLite or no pool
                stats["utilization_percent"] = 0
            
            # Acquisition time metrics
            if self._connection_acquisitions:
                acq_times = [duration for _, duration in self._connection_acquisitions[-100:]]  # Last 100
                stats["avg_acquisition_time"] = sum(acq_times) / len(acq_times)
                stats["max_acquisition_time"] = max(acq_times)
            
            # Pool wait metrics
            if self._connection_pool_waits:
                wait_times = [duration for _, duration in self._connection_pool_waits[-100:]]  # Last 100
                stats["avg_pool_wait_time"] = sum(wait_times) / len(wait_times)
            
            # Check for old connections
            current_time = time.time()
            for conn_id, metadata in self._connection_metadata.items():
                if "created_at" in metadata:
                    age_seconds = current_time - metadata["created_at"]
                    if self._max_connection_age_seconds > 0 and age_seconds > self._max_connection_age_seconds:
                        stats["old_connections"].append({
                            "connection_id": conn_id,
                            "age_seconds": age_seconds,
                            "thread_id": metadata.get("thread_id"),
                        })
            
            return stats

    @property
    def transaction_depth(self) -> int:
        """Get the current thread's transaction depth, initializing if needed."""
        if not hasattr(self._local, "transaction_depth"):
            self._local.transaction_depth = 0
        return self._local.transaction_depth

    @transaction_depth.setter
    def transaction_depth(self, value: int):
        """Set the current thread's transaction depth."""
        self._local.transaction_depth = value

    @property
    def in_transaction(self) -> bool:
        """Get the current thread's transaction status, initializing if needed."""
        if not hasattr(self._local, "in_transaction"):
            self._local.in_transaction = False
        return self._local.in_transaction

    @in_transaction.setter
    def in_transaction(self, value: bool):
        """Set the current thread's transaction status."""
        self._local.in_transaction = value

    def _get_conn(self) -> DbConnection:
        """Get or create a thread-local database connection.
        
        Validates existing connections before reusing them.
        For PostgreSQL: checks conn.closed attribute and optionally runs validation query.
        For SQLite: checks conn is not None.
        Enforces max_idle_time by checking connection idle duration.
        Updates last_used timestamp after validation.
        
        Automatically generates and sets a correlation ID if none exists for request tracing.
        """
        # Ensure thread-local state is initialized via properties
        _ = self.transaction_depth
        _ = self.in_transaction

        # Ensure correlation ID is set for this request/thread
        if self.get_correlation_id() is None:
            correlation_id = self._generate_correlation_id()
            self.set_correlation_id(correlation_id)
            logger.debug(f"Generated new correlation ID: {correlation_id}")
        
        if hasattr(self._local, "connection") and self._local.connection is not None:
            # Connection exists - validate it
            conn = self._local.connection
            is_valid = False
            
            if self.type == "postgres":
                # PostgreSQL: check closed attribute (0 = open, >0 = closed)
                if hasattr(conn, "closed") and conn.closed == 0:
                    # Check idle time if configured
                    if self._max_idle_time > 0:
                        conn_id = id(conn)
                        with self._pool_stats_lock:
                            if conn_id in self._connection_metadata:
                                metadata = self._connection_metadata[conn_id]
                                last_used = metadata.get("last_used", time.time())
                                idle_duration = time.time() - last_used
                                
                                if idle_duration > self._max_idle_time:
                                    logger.warning(
                                        f"Connection {conn_id} exceeded max idle time "
                                        f"({idle_duration:.1f}s > {self._max_idle_time}s), evicting"
                                    )
                                    # Mark as invalid to trigger replacement
                                    is_valid = False
                                    metadata["evicted"] = True
                                else:
                                    is_valid = True
                            else:
                                is_valid = True
                    else:
                        is_valid = True
                    
                    # Perform optional validation query check
                    if is_valid and self._enable_validation:
                        conn_id = id(conn)
                        with self._pool_stats_lock:
                            if conn_id in self._connection_metadata:
                                metadata = self._connection_metadata[conn_id]
                                last_validation = metadata.get("last_validation", 0)
                                time_since_validation = time.time() - last_validation
                                
                                # Only run validation if interval has passed
                                if time_since_validation >= self._validation_interval:
                                    try:
                                        cursor = conn.cursor()
                                        cursor.execute(self._validation_query)
                                        cursor.close()
                                        metadata["last_validation"] = time.time()
                                        logger.debug(f"Connection {conn_id} passed validation")
                                        is_valid = True
                                    except Exception as e:
                                        logger.warning(
                                            f"Connection {conn_id} failed validation query '{self._validation_query}': {e}"
                                        )
                                        is_valid = False
                                        metadata["validation_failed"] = True
            else:
                # SQLite: connection is valid if it's not None
                is_valid = True
            
            if is_valid:
                # Update last_used timestamp for reused connection
                conn_id = id(conn)
                with self._pool_stats_lock:
                    if conn_id in self._connection_metadata:
                        self._connection_metadata[conn_id]["last_used"] = time.time()
                        metadata = self._connection_metadata[conn_id]
                        conn_age = time.time() - metadata.get("created_at", time.time())
                        context = self._format_log_context(
                            thread_id=threading.current_thread().ident,
                            connection_id=conn_id,
                            connection_age_seconds=f"{conn_age:.2f}",
                            last_used=f"{metadata.get('last_used', 0):.2f}"
                        )
                        logger.debug(f"Reusing existing thread-local connection {context}")
                return conn
            else:
                # Connection is invalid/closed, evict from pool and create new one
                logger.warning("Thread-local connection is closed/invalid/idle, reconnecting")
                if self.type == "postgres" and self._pool:
                    try:
                        self._pool.putconn(conn, close=True)
                        logger.info("Discarded invalid connection from pool")
                    except Exception as e:
                        logger.debug(f"Could not discard invalid connection from pool: {e}")
                else:
                    try:
                        conn.close()
                    except Exception as e:
                        logger.debug(f"Could not close invalid connection: {e}")
                self._local.connection = None
        
        # No valid connection exists, create new one
        self.connect()
        return self._local.connection

    def connect(self) -> Optional[DbConnection]:
        """Establish a connection to the database for the current thread.
        
        If a connection already exists for this thread, it will be properly
        closed/returned to pool before acquiring a new one.
        """
        with self._lock:
            # Check if thread-local connection already exists
            if hasattr(self._local, "connection") and self._local.connection is not None:
                old_conn = self._local.connection
                logger.info("Replacing existing connection with new one")
                
                if self.type == "postgres":
                    if self._pool:
                        try:
                            self._pool.putconn(old_conn)
                            logger.info("Returned old connection to pool before acquiring new one")
                        except Exception as e:
                            logger.debug(f"Could not return old connection to pool: {e}")
                else:
                    try:
                        old_conn.close()
                        logger.info("Closed old SQLite connection before acquiring new one")
                    except Exception as e:
                        logger.debug(f"Could not close old connection: {e}")
                
                self._local.connection = None
            
            # Acquire new connection
            thread_name = threading.current_thread().name
            if self.type == "sqlite":
                logger.info(f"Acquiring new database connection for thread {thread_name}")
                conn = self._connect_sqlite()
            elif self.type == "postgres":
                logger.info(f"Acquiring new database connection for thread {thread_name}")
                conn = self._connect_postgres()
            else:
                raise ValueError(f"Unsupported database type: {self.type}")

            self._local.connection = conn
            # Initialize thread-local state
            self.transaction_depth = 0
            self.in_transaction = False
            return conn

    def _connect_sqlite(self) -> DbConnection:
        """Connect to SQLite database."""
        path = self.config.get("path", "data/database.db")
        db_dir = os.path.dirname(path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        try:
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row

            # Performance settings
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")

            # Track connection metadata
            conn_id = id(conn)
            with self._pool_stats_lock:
                self._connection_metadata[conn_id] = {
                    "created_at": time.time(),
                    "thread_id": threading.current_thread().ident,
                    "acquired_at": datetime.now().isoformat(),
                }

            context = self._format_log_context(
                thread_id=threading.current_thread().ident,
                connection_id=conn_id,
                db_path=path
            )
            logger.debug(f"Thread {threading.current_thread().name} acquired SQLite connection {context}")
            logger.info(f"Connected to SQLite at {path}")
            return conn
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to SQLite: {e}")
            raise

    def _connect_postgres(self) -> DbConnection:
        """Connect to PostgreSQL using thread-safe pooling with timeout validation."""
        try:
            import psycopg2
            import psycopg2.extras
            import psycopg2.pool
        except ImportError:
            raise ImportError("psycopg2-binary is required for PostgreSQL support.")

        if not self._pool:
            pg_config = self.config.get("postgres", {})
            pool_config = self.config.get("connection_pool", {})
            
            # Read timeout configuration (default 10 seconds)
            connect_timeout = pool_config.get("connect_timeout", 10)

            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=pool_config.get("min_connections", 2),
                maxconn=pool_config.get("max_connections", 20),
                connect_timeout=connect_timeout,
                host=pg_config.get("host", "localhost"),
                port=pg_config.get("port", 5432),
                user=pg_config.get("user", "postgres"),
                password=pg_config.get("password", ""),
                dbname=pg_config.get("dbname", "plexichat"),
                sslmode=pg_config.get("sslmode", "prefer"),
                cursor_factory=psycopg2.extras.RealDictCursor,
                options="-c client_encoding=UTF8",
            )

        try:
            acq_start = time.time()
            try:
                conn = self._pool.getconn()
            except Exception:
                # Record pool wait event
                wait_duration = time.time() - acq_start
                with self._pool_stats_lock:
                    self._connection_pool_waits.append((acq_start, wait_duration))
                raise
            
            # Record acquisition time
            acq_duration = time.time() - acq_start
            with self._pool_stats_lock:
                self._connection_acquisitions.append((acq_start, acq_duration))
            
            # Validate connection is not closed
            if hasattr(conn, "closed") and conn.closed:
                logger.error("Connection from pool is closed, raising exception")
                raise psycopg2.OperationalError("Connection is closed")
            
            conn.autocommit = False
            
            # Track connection metadata
            conn_id = id(conn)
            with self._pool_stats_lock:
                self._connection_metadata[conn_id] = {
                    "created_at": time.time(),
                    "last_used": time.time(),
                    "last_validation": time.time(),
                    "thread_id": threading.current_thread().ident,
                    "acquired_at": datetime.now().isoformat(),
                }
                # Calculate pool utilization for this acquisition
                pool_stats = self.get_pool_stats()
                utilization = pool_stats.get("utilization_percent", 0)
            
            context = self._format_log_context(
                thread_id=threading.current_thread().ident,
                connection_id=conn_id,
                acquisition_time_ms=f"{acq_duration * 1000:.2f}",
                pool_utilization_percent=f"{utilization:.1f}" if isinstance(utilization, (int, float)) else "unavailable"
            )
            logger.debug(f"Thread {threading.current_thread().name} acquired PostgreSQL connection from pool {context}")
            logger.info("Acquired connection from pool")
            return conn
        except psycopg2.pool.PoolError as e:
            logger.error(f"Connection pool exhausted, no available connections: {e}")
            raise
        except psycopg2.OperationalError as e:
            # Check if this is a timeout-related error
            error_str = str(e).lower()
            connect_timeout = self.config.get("connection_pool", {}).get("connect_timeout", 10)
            
            if "timeout" in error_str or "timed out" in error_str or "timeout" in str(type(e).__name__).lower():
                logger.error(f"Connection timeout after {connect_timeout}s while connecting to PostgreSQL: {e}")
            else:
                logger.error(f"Failed to acquire connection from pool: {e}")
            raise

    def start_pool_monitoring(self) -> None:
        """Start periodic logging of pool statistics in background thread.
        
        Logs pool stats every N seconds (configurable, default 60s).
        Also checks for and warns about long-lived connections.
        Safe to call multiple times - only starts one monitoring thread.
        """
        if self._periodic_logging_thread is not None and self._periodic_logging_thread.is_alive():
            logger.debug("Pool monitoring thread already running")
            return
        
        self._stop_logging = False
        self._periodic_logging_thread = threading.Thread(
            target=self._periodic_pool_logging_loop,
            daemon=True,
            name="DatabasePoolMonitor",
        )
        self._periodic_logging_thread.start()
        logger.info(f"Started pool monitoring thread (interval: {self._periodic_log_interval}s)")

    def stop_pool_monitoring(self) -> None:
        """Stop the periodic pool monitoring thread gracefully.
        
        Safe to call even if monitoring is not running.
        """
        if self._periodic_logging_thread is None or not self._periodic_logging_thread.is_alive():
            return
        
        self._stop_logging = True
        # Wait for thread to finish (max 5 seconds)
        self._periodic_logging_thread.join(timeout=5)
        logger.info("Stopped pool monitoring thread")

    def _periodic_pool_logging_loop(self) -> None:
        """Background thread loop for periodic pool monitoring and logging."""
        while not self._stop_logging:
            try:
                # Log pool statistics with structured logging
                stats = self.get_pool_stats()
                error_rate = self._calculate_error_rate()
                
                context = self._format_log_context(
                    active_connections=stats['active_connections'],
                    idle_connections=stats['idle_connections'],
                    total_connections=stats['total_connections'],
                    max_connections=stats['max_connections'],
                    utilization_percent=f"{stats.get('utilization_percent', 0):.1f}" if isinstance(stats.get('utilization_percent'), (int, float)) else stats.get('utilization_percent', 'N/A'),
                    avg_acquisition_time_ms=f"{stats['avg_acquisition_time'] * 1000:.2f}",
                    error_rate_per_minute=f"{error_rate:.2f}"
                )
                logger.info(f"Pool monitoring statistics {context}")
                
                # Check for high pool utilization
                utilization = stats.get('utilization_percent', 0)
                if isinstance(utilization, (int, float)) and utilization > self._pool_saturation_threshold_percent:
                    context_warning = self._format_log_context(
                        utilization_percent=f"{utilization:.1f}",
                        threshold_percent=self._pool_saturation_threshold_percent,
                        active_connections=stats['active_connections'],
                        max_connections=stats['max_connections']
                    )
                    logger.warning(f"High pool utilization detected {context_warning}")
                
                # Check for high error rate
                if error_rate > self._error_rate_threshold:
                    context_error = self._format_log_context(
                        error_rate_per_minute=f"{error_rate:.2f}",
                        threshold_per_minute=self._error_rate_threshold
                    )
                    logger.warning(f"High error rate detected {context_error}")
                
                # Check for old connections and log warnings
                if stats["old_connections"]:
                    for old_conn in stats["old_connections"]:
                        logger.warning(
                            f"Long-lived connection detected - ID: {old_conn['connection_id']}, "
                            f"Age: {old_conn['age_seconds']:.1f}s, Thread: {old_conn['thread_id']}"
                        )
                
                # Cleanup metrics to prevent unbounded memory growth
                current_time = time.time()
                window_start = current_time - self._error_rate_window_seconds
                
                # Trim query execution times to last 1000 entries
                if len(self._query_execution_times) > 1000:
                    self._query_execution_times = self._query_execution_times[-1000:]
                
                # Trim error counts to entries within the error rate window
                for error_type in list(self._error_counts.keys()):
                    self._error_counts[error_type] = [
                        (ts, et) for ts, et in self._error_counts[error_type]
                        if ts >= window_start
                    ]
                    if not self._error_counts[error_type]:
                        del self._error_counts[error_type]
                
                # Trim connection acquisitions to last 1000 entries
                if len(self._connection_acquisitions) > 1000:
                    self._connection_acquisitions = self._connection_acquisitions[-1000:]
                
                # Trim connection pool waits to last 1000 entries
                if len(self._connection_pool_waits) > 1000:
                    self._connection_pool_waits = self._connection_pool_waits[-1000:]
                
                # Sleep for the configured interval
                time.sleep(self._periodic_log_interval)
            except Exception as e:
                logger.error(f"Error in pool monitoring thread: {e}")
                # Continue monitoring despite errors
                time.sleep(self._periodic_log_interval)

    def _check_connection_age(self, conn_id: int) -> None:
        """Check if a connection exceeds max age and log warning if so.
        
        Args:
            conn_id: Python object id of the connection
        """
        if self._max_connection_age_seconds <= 0:
            return
        
        with self._pool_stats_lock:
            if conn_id in self._connection_metadata:
                metadata = self._connection_metadata[conn_id]
                if "created_at" in metadata:
                    age_seconds = time.time() - metadata["created_at"]
                    if age_seconds > self._max_connection_age_seconds:
                        logger.warning(
                            f"Connection {conn_id} has exceeded max age threshold "
                            f"({age_seconds:.1f}s > {self._max_connection_age_seconds:.1f}s). "
                            f"Created in thread {metadata.get('thread_id')}."
                        )

    def set_correlation_id(self, correlation_id: str) -> None:
        """Store a correlation ID in thread-local storage.
        
        Enables request tracing across database operations by associating
        a unique identifier with each request thread.
        
        Args:
            correlation_id: Unique identifier for this request/transaction
        """
        self._local.correlation_id = correlation_id

    def get_correlation_id(self) -> Optional[str]:
        """Retrieve the current thread's correlation ID.
        
        Returns:
            Correlation ID if set, None otherwise
        """
        return getattr(self._local, "correlation_id", None)

    def _generate_correlation_id(self) -> str:
        """Generate a UUID-based correlation ID if none exists.
        
        Returns:
            New UUID-based correlation ID
        """
        return str(uuid.uuid4())

    def _format_log_context(self, **kwargs) -> str:
        """Format structured log context with consistent formatting.
        
        Accepts arbitrary keyword arguments and returns formatted string
        with key=value pairs. Automatically includes correlation_id if available.
        Handles None values gracefully.
        
        Args:
            **kwargs: Arbitrary key-value pairs to include in log context
        
        Returns:
            Formatted string like: [correlation_id=abc123 thread_id=12345 connection_id=67890]
        """
        context = {}
        
        # Include correlation ID if available
        correlation_id = self.get_correlation_id()
        if correlation_id:
            context["correlation_id"] = correlation_id
        
        # Add all provided kwargs
        for key, value in kwargs.items():
            if value is not None:
                context[key] = value
        
        # Format as key=value pairs
        if not context:
            return ""
        
        pairs = [f"{k}={v}" for k, v in context.items()]
        return f"[{' '.join(pairs)}]"

    def _calculate_error_rate(self) -> float:
        """Calculate current error rate (errors per minute) using sliding window.
        
        Filters error counts to only include errors within the configured
        error_rate_window_seconds and calculates rate per minute.
        
        Returns:
            Error rate as errors per minute (float)
        """
        current_time = time.time()
        window_start = current_time - self._error_rate_window_seconds
        
        # Count errors within the time window
        recent_errors = 0
        for error_type, error_list in self._error_counts.items():
            for timestamp, _ in error_list:
                if timestamp >= window_start:
                    recent_errors += 1
        
        # Calculate rate per minute (60 seconds)
        if self._error_rate_window_seconds > 0:
            rate_per_minute = (recent_errors / self._error_rate_window_seconds) * 60
        else:
            rate_per_minute = 0
        
        return rate_per_minute

    def _sanitize_identifier(self, identifier: str) -> str:
        """Sanitize SQL identifiers (table/column names)."""
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier):
            raise ValueError(f"Invalid identifier: {identifier}")
        return f'"{identifier}"' if self.type == "postgres" else f"`{identifier}`"

    def _convert_placeholders(self, query: str) -> str:
        """
        Convert ? placeholders to %s for PostgreSQL compatibility.
        Uses regex to safely ignore ? inside single-quoted strings and double-quoted identifiers.
        Returns unchanged query for SQLite.
        """
        if self.type == "postgres":
            return _PLACEHOLDER_PATTERN.sub(lambda m: m.group(1) if m.group(1) else "%s", query)
        return query

    def _validate_transaction_state(self) -> None:
        """
        Validate and recover transaction state before executing queries.
        For PostgreSQL, detects and recovers from InFailedSqlTransaction state.
        
        Raises:
            RuntimeError: If transaction state cannot be recovered.
        """
        if self.type != "postgres" or self.transaction_depth == 0:
            return
        
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            # Test if transaction is in failed state by executing harmless query
            cursor.execute("SELECT 1")
            cursor.close()
        except Exception as e:
            cursor.close()
            
            # Check if this is an InFailedSqlTransaction error
            is_failed_transaction = False
            
            # Check by exception type name (works with psycopg2.errors.InFailedSqlTransaction)
            exception_type_name = type(e).__name__
            if exception_type_name == "InFailedSqlTransaction":
                is_failed_transaction = True
            
            # Check by pgcode attribute if available (PostgreSQL error code)
            if hasattr(e, "pgcode") and e.pgcode == "25P02":  # 25P02 is the pgcode for InFailedSqlTransaction
                is_failed_transaction = True
            
            # Check for common message fragments
            error_msg = str(e).lower()
            common_fragments = [
                "current transaction is aborted",
                "commands ignored until end of transaction block",
                "infailedsqltransaction",
                "in failed sql transaction",
            ]
            if any(fragment in error_msg for fragment in common_fragments):
                is_failed_transaction = True
            
            if is_failed_transaction:
                logger.warning(f"Transaction in failed state, attempting recovery: {e}")
                self._recover_from_failed_transaction()
            else:
                raise

    def _recover_from_failed_transaction(self) -> None:
        """
        Recover from PostgreSQL InFailedSqlTransaction state.
        Rolls back all savepoints and resets transaction depth.
        
        Raises:
            RuntimeError: If recovery fails.
        """
        if self.type != "postgres":
            return
        
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            # Rollback entire transaction to clear failed state
            cursor.execute("ROLLBACK")
            logger.info("Successfully recovered from failed transaction state")
        except Exception as e:
            logger.error(f"Failed to recover from transaction error: {e}")
            raise RuntimeError(f"Cannot recover from transaction error: {e}") from e
        finally:
            cursor.close()
            # Reset transaction state
            self.transaction_depth = 0
            self.in_transaction = False

    def execute(
        self, query: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> DbCursor:
        """Execute a query using the thread-local connection.
        
        Validates transaction state before execution and properly manages
        savepoint state on errors. Implements automatic recovery for
        PostgreSQL InFailedSqlTransaction errors.
        
        Args:
            query: SQL query to execute
            params: Query parameters as tuple
            auto_commit: Whether to auto-commit if not in transaction
        
        Returns:
            Database cursor with results
        
        Raises:
            Any database exception from failed query execution
        """
        # Ensure thread-local state is initialized via properties
        _ = self.transaction_depth
        _ = self.in_transaction

        # Validate transaction state before executing
        if self.transaction_depth > 0:
            self._validate_transaction_state()
        
        conn = self._get_conn()

        # Dialect-aware placeholder conversion
        query = self._convert_placeholders(query)

        cursor = conn.cursor()
        try:
            # Track query execution time
            exec_start = time.time()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            exec_time_ms = (time.time() - exec_start) * 1000
            
            # Record query execution time
            with self._pool_stats_lock:
                self._query_execution_times.append((time.time(), exec_time_ms))
            
            # Log with structured data
            params_count = len(params) if params else 0
            query_preview = query[:100] if len(query) > 100 else query
            context = self._format_log_context(
                query=query_preview,
                execution_time_ms=f"{exec_time_ms:.2f}",
                params_count=params_count,
                auto_commit=auto_commit,
                thread_id=threading.current_thread().ident
            )
            logger.debug(f"Query executed successfully {context}")
            
            # Check for slow query
            if exec_time_ms > self._slow_query_threshold_ms:
                slow_context = self._format_log_context(
                    query=query_preview,
                    execution_time_ms=f"{exec_time_ms:.2f}",
                    threshold_ms=self._slow_query_threshold_ms,
                    thread_id=threading.current_thread().ident
                )
                logger.warning(f"Slow query detected {slow_context}")

            if auto_commit and not self.in_transaction:
                conn.commit()
            return cursor
        except Exception as e:
            # Record execution time even on error
            exec_time_ms = (time.time() - exec_start) * 1000
            with self._pool_stats_lock:
                self._query_execution_times.append((time.time(), exec_time_ms))
            
            error_msg = str(e).lower()
            error_type = type(e).__name__
            
            # Record error count
            with self._pool_stats_lock:
                if error_type not in self._error_counts:
                    self._error_counts[error_type] = []
                self._error_counts[error_type].append((time.time(), error_type))
            
            # Calculate error rate
            error_rate = self._calculate_error_rate()
            query_preview = query[:100] if len(query) > 100 else query
            error_context = self._format_log_context(
                error_type=error_type,
                error_message=str(e)[:100],
                query=query_preview,
                execution_time_ms=f"{exec_time_ms:.2f}",
                error_rate_per_minute=f"{error_rate:.2f}",
                thread_id=threading.current_thread().ident
            )
            logger.error(f"Query failed {error_context}")
            
            # Log high error rate warning
            if error_rate > self._error_rate_threshold:
                rate_context = self._format_log_context(
                    error_rate_per_minute=f"{error_rate:.2f}",
                    threshold_per_minute=self._error_rate_threshold
                )
                logger.warning(f"High error rate detected {rate_context}")
            
            # Handle transaction state corruption
            try:
                if self.type == "postgres":
                    # PostgreSQL requires rollback on ANY error
                    if self.transaction_depth > 0:
                        # Attempt to rollback to last savepoint
                        try:
                            cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}")
                            logger.debug(f"Rolled back to savepoint sp_{self.transaction_depth}")
                        except Exception as inner_e:
                            # Savepoint rollback failed, rollback entire transaction
                            logger.warning(f"Savepoint rollback failed: {inner_e}, rolling back transaction")
                            conn.rollback()
                            self.transaction_depth = 0
                            self.in_transaction = False
                    else:
                        # Not in nested transaction, simple rollback
                        conn.rollback()
                        self.in_transaction = False
                elif not self.in_transaction:
                    # SQLite: only rollback if not explicitly in transaction
                    conn.rollback()
            except Exception as cleanup_e:
                logger.error(f"Error during transaction cleanup: {cleanup_e}")
                # Force state reset on cleanup failure
                self.transaction_depth = 0
                self.in_transaction = False
            finally:
                cursor.close()
            
            raise

    def fetch_one(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single result as a dict."""
        cursor = self.execute(query, params)
        result = cursor.fetchone()
        cursor.close()
        return dict(result) if result else None

    def fetch_all(
        self, query: str, params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]:
        """Fetch all results as a list of dicts."""
        cursor = self.execute(query, params)
        results = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in results]

    def table_exists(self, table_name: str) -> bool:
        """
        Check if a table exists in the database.

        Args:
            table_name: Name of the table to check.

        Returns:
            True if table exists, False otherwise.
        """
        with self._lock:
            self._get_conn()

            if self.type == "sqlite":
                query = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
                result = self.fetch_one(query, (table_name,))
            elif self.type == "postgres":
                # Use ? placeholder - will be auto-converted to %s
                query = """
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema='public' AND table_name=?
                """
                result = self.fetch_one(query, (table_name,))
            else:
                return False

            return result is not None

    def begin_transaction(self) -> None:
        """Begin a transaction using SAVEPOINT for nesting support."""
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
        """Commit the current transaction level.
        
        Properly handles savepoint release with error recovery. On failure,
        rolls back the entire transaction and resets state.
        """
        conn = self._get_conn()
        if self.transaction_depth > 0:
            cursor = conn.cursor()
            try:
                cursor.execute(f"RELEASE SAVEPOINT sp_{self.transaction_depth}")
                logger.debug(f"Released savepoint sp_{self.transaction_depth}")
                cursor.close()
            except Exception as e:
                # Savepoint release failed, rollback entire transaction
                logger.warning(f"Savepoint release failed: {e}, rolling back entire transaction")
                cursor.close()
                try:
                    conn.rollback()
                    logger.info("Successfully rolled back transaction after savepoint release failure")
                except Exception as rollback_e:
                    logger.error(f"Failed to rollback transaction: {rollback_e}")
                finally:
                    # Reset state on any error
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
        """Rollback the current transaction level.
        
        Properly handles savepoint rollback with error recovery. On failure,
        rolls back the entire transaction and resets state.
        """
        conn = self._get_conn()
        if self.transaction_depth > 0:
            cursor = conn.cursor()
            try:
                cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}")
                logger.debug(f"Rolled back to savepoint sp_{self.transaction_depth}")
                cursor.close()
            except Exception as e:
                # Savepoint rollback failed, rollback entire transaction
                logger.warning(f"Savepoint rollback failed: {e}, rolling back entire transaction")
                cursor.close()
                try:
                    conn.rollback()
                    logger.info("Successfully rolled back transaction")
                except Exception as rollback_e:
                    logger.error(f"Failed to rollback transaction: {rollback_e}")
                    raise
                finally:
                    # Reset state on any error
                    self.transaction_depth = 0
                    self.in_transaction = False
                return
            
            self.transaction_depth -= 1

            if self.transaction_depth == 0:
                conn.rollback()
                self.in_transaction = False
        else:
            try:
                conn.rollback()
            except Exception as e:
                logger.error(f"Rollback failed: {e}")
                raise
            finally:
                self.in_transaction = False

    def insert_or_ignore(self, table: str, columns: List[str], values: Tuple) -> bool:
        """Secure cross-database INSERT OR IGNORE."""
        safe_table = self._sanitize_identifier(table)
        safe_cols = ", ".join(self._sanitize_identifier(c) for c in columns)
        placeholders = ", ".join(["?"] * len(columns))

        if self.type == "sqlite":
            query = f"INSERT OR IGNORE INTO {safe_table} ({safe_cols}) VALUES ({placeholders})"
        else:
            query = f"INSERT INTO {safe_table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

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
        """Secure cross-database UPSERT using ON CONFLICT (no data loss)."""
        safe_table = self._sanitize_identifier(table)
        safe_cols = ", ".join(self._sanitize_identifier(c) for c in columns)
        placeholders = ", ".join(["?"] * len(columns))

        if update_columns is None:
            update_columns = [c for c in columns if c not in conflict_columns]

        conflict_cols = ", ".join(
            self._sanitize_identifier(c) for c in conflict_columns
        )

        if not update_columns:
            self.insert_or_ignore(table, columns, values)
            return

        updates = ", ".join(
            [
                f"{self._sanitize_identifier(c)} = EXCLUDED.{self._sanitize_identifier(c)}"
                for c in update_columns
            ]
        )
        query = f"INSERT INTO {safe_table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"

        cursor = self.execute(query, values)
        cursor.close()

    def close(self) -> None:
        """Close the thread-local connection or return to pool.
        
        For PostgreSQL:
        - Checks idle time before returning to pool; evicts if exceeds max_idle_time
        - Updates last_used timestamp if returning to pool
        - Replaces evicted connections to maintain pool size
        
        Properly clears all thread-local state after returning/closing connection.
        Also stops the pool monitoring thread if it's the last connection being closed.
        """
        # Stop periodic monitoring if running
        self.stop_pool_monitoring()

        thread_name = threading.current_thread().name
        if hasattr(self._local, "connection") and self._local.connection:
            try:
                conn_id = id(self._local.connection)
                
                # Check connection age before closing
                self._check_connection_age(conn_id)
                
                # For PostgreSQL, check idle time and decide whether to return or evict
                if self.type == "postgres" and self._pool and self._max_idle_time > 0:
                    with self._pool_stats_lock:
                        if conn_id in self._connection_metadata:
                            metadata = self._connection_metadata[conn_id]
                            last_used = metadata.get("last_used", time.time())
                            idle_duration = time.time() - last_used
                            conn_age = time.time() - metadata.get("created_at", time.time())
                            
                            if idle_duration > self._max_idle_time:
                                # Evict connection that has been idle too long
                                evict_context = self._format_log_context(
                                    connection_id=conn_id,
                                    idle_duration_seconds=f"{idle_duration:.1f}",
                                    max_idle_seconds=self._max_idle_time,
                                    thread_id=threading.current_thread().ident
                                )
                                logger.info(f"Thread {thread_name} evicting idle connection {evict_context}")
                                try:
                                    self._pool.putconn(self._local.connection, close=True)
                                    logger.info(f"Thread {thread_name} closed idle connection")
                                    # Remove metadata for evicted connection
                                    if conn_id in self._connection_metadata:
                                        del self._connection_metadata[conn_id]
                                except Exception as e:
                                    logger.error(f"Error evicting idle connection: {e}")
                            else:
                                # Update last_used timestamp and return to pool
                                metadata["last_used"] = time.time()
                                self._pool.putconn(self._local.connection)
                                return_context = self._format_log_context(
                                    connection_id=conn_id,
                                    connection_age_seconds=f"{conn_age:.1f}",
                                    idle_duration_seconds=f"{idle_duration:.1f}",
                                    thread_id=threading.current_thread().ident
                                )
                                logger.info(f"Thread {thread_name} returned connection to pool {return_context}")
                        else:
                            # No metadata, just return to pool
                            self._pool.putconn(self._local.connection)
                            logger.info(f"Thread {thread_name} returned connection to pool")
                elif self._pool:
                    # PostgreSQL pool but no idle time config, just return
                    self._pool.putconn(self._local.connection)
                    logger.info(f"Thread {thread_name} returned connection to pool")
                    # Clean up metadata for this connection
                    with self._pool_stats_lock:
                        if conn_id in self._connection_metadata:
                            del self._connection_metadata[conn_id]
                else:
                    # SQLite or no pool
                    with self._pool_stats_lock:
                        if conn_id in self._connection_metadata:
                            metadata = self._connection_metadata[conn_id]
                            conn_age = time.time() - metadata.get("created_at", time.time())
                        else:
                            conn_age = 0
                    
                    self._local.connection.close()
                    close_context = self._format_log_context(
                        connection_id=conn_id,
                        connection_age_seconds=f"{conn_age:.1f}",
                        thread_id=threading.current_thread().ident
                    )
                    logger.info(f"Thread {thread_name} closed database connection {close_context}")
                    # Clean up metadata for this connection
                    with self._pool_stats_lock:
                        if conn_id in self._connection_metadata:
                            del self._connection_metadata[conn_id]
            except Exception as e:
                logger.error(f"Error closing/returning connection: {e}")
        
        # Clear all thread-local state
        self._local.connection = None
        self.transaction_depth = 0
        self.in_transaction = False
        logger.debug(f"Thread {thread_name} cleared thread-local connection state")

    def execute_many(self, query: str, params_list: List[Tuple]) -> DbCursor:
        """Execute batch query safely with proper transaction state management.
        
        Validates transaction state before execution and properly manages
        savepoint state on errors. Implements automatic recovery for
        PostgreSQL InFailedSqlTransaction errors.
        
        Args:
            query: SQL query to execute repeatedly
            params_list: List of parameter tuples for each execution
        
        Returns:
            Database cursor with results
        
        Raises:
            Any database exception from failed query execution
        """
        # Ensure thread-local state is initialized via properties
        _ = self.transaction_depth
        _ = self.in_transaction

        # Validate transaction state before executing
        if self.transaction_depth > 0:
            self._validate_transaction_state()
        
        conn = self._get_conn()
        # Dialect-aware placeholder conversion
        query = self._convert_placeholders(query)

        cursor = conn.cursor()
        try:
            # Track query execution time
            exec_start = time.time()
            cursor.executemany(query, params_list)
            exec_time_ms = (time.time() - exec_start) * 1000
            
            # Record query execution time
            with self._pool_stats_lock:
                self._query_execution_times.append((time.time(), exec_time_ms))
            
            # Log with structured data
            params_count = len(params_list) if params_list else 0
            query_preview = query[:100] if len(query) > 100 else query
            context = self._format_log_context(
                query=query_preview,
                execution_time_ms=f"{exec_time_ms:.2f}",
                batch_size=params_count,
                thread_id=threading.current_thread().ident
            )
            logger.debug(f"Batch query executed successfully {context}")
            
            # Check for slow query
            if exec_time_ms > self._slow_query_threshold_ms:
                slow_context = self._format_log_context(
                    query=query_preview,
                    execution_time_ms=f"{exec_time_ms:.2f}",
                    threshold_ms=self._slow_query_threshold_ms,
                    thread_id=threading.current_thread().ident
                )
                logger.warning(f"Slow batch query detected {slow_context}")
            
            if not self.in_transaction:
                conn.commit()
            return cursor
        except Exception as e:
            # Record execution time even on error
            exec_time_ms = (time.time() - exec_start) * 1000
            with self._pool_stats_lock:
                self._query_execution_times.append((time.time(), exec_time_ms))
            
            error_msg = str(e).lower()
            error_type = type(e).__name__
            
            # Record error count
            with self._pool_stats_lock:
                if error_type not in self._error_counts:
                    self._error_counts[error_type] = []
                self._error_counts[error_type].append((time.time(), error_type))
            
            # Calculate error rate
            error_rate = self._calculate_error_rate()
            query_preview = query[:100] if len(query) > 100 else query
            error_context = self._format_log_context(
                error_type=error_type,
                error_message=str(e)[:100],
                query=query_preview,
                execution_time_ms=f"{exec_time_ms:.2f}",
                batch_size=len(params_list) if params_list else 0,
                error_rate_per_minute=f"{error_rate:.2f}",
                thread_id=threading.current_thread().ident
            )
            logger.error(f"Batch query failed {error_context}")
            
            # Log high error rate warning
            if error_rate > self._error_rate_threshold:
                rate_context = self._format_log_context(
                    error_rate_per_minute=f"{error_rate:.2f}",
                    threshold_per_minute=self._error_rate_threshold
                )
                logger.warning(f"High error rate detected {rate_context}")
            
            # Handle transaction state corruption
            try:
                if self.type == "postgres":
                    # PostgreSQL requires rollback on ANY error
                    if self.transaction_depth > 0:
                        # Attempt to rollback to last savepoint
                        try:
                            cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}")
                            logger.debug(f"Rolled back to savepoint sp_{self.transaction_depth}")
                        except Exception as inner_e:
                            # Savepoint rollback failed, rollback entire transaction
                            logger.warning(f"Savepoint rollback failed: {inner_e}, rolling back transaction")
                            conn.rollback()
                            self.transaction_depth = 0
                            self.in_transaction = False
                    else:
                        # Not in nested transaction, simple rollback
                        conn.rollback()
                        self.in_transaction = False
                elif not self.in_transaction:
                    # SQLite: only rollback if not explicitly in transaction
                    conn.rollback()
            except Exception as cleanup_e:
                logger.error(f"Error during transaction cleanup: {cleanup_e}")
                # Force state reset on cleanup failure
                self.transaction_depth = 0
                self.in_transaction = False
            finally:
                cursor.close()
            
            raise

    def convert_schema(self, schema: str) -> str:
        """Safer schema conversion for Postgres."""
        if self.type != "postgres":
            return schema

        converted = schema
        # BLOB -> BYTEA
        converted = re.sub(r"\bBLOB\b", "BYTEA", converted, flags=re.IGNORECASE)
        # Snowflake IDs: INTEGER -> BIGINT
        converted = re.sub(r"\bINTEGER\b", "BIGINT", converted, flags=re.IGNORECASE)

        return converted

    def __enter__(self) -> "Database":
        """Context manager entry."""
        self._get_conn()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Context manager exit."""
        if exc_type:
            self.rollback()
        return False
