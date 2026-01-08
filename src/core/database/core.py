"""
Database module - Provides database connectivity for SQLite and PostgreSQL.

This module follows the zero-friction pattern established by common-utils.
It requires config and logger to be set up before use.

PostgreSQL Support:
    - Uses psycopg2-binary driver (install with: pip install psycopg2-binary)
    - Automatically converts ? placeholders to %s for PostgreSQL compatibility
    - Uses RealDictCursor for dict-like row access matching SQLite behavior
"""

import sqlite3
import os
import re
import threading
from typing import Any, List, Optional, Tuple, Union, Dict

import utils.config as config
import utils.logger as logger

# Type alias for database connections
DbConnection = Union[sqlite3.Connection, Any]  # Any for psycopg2 connection
DbCursor = Union[sqlite3.Cursor, Any]  # Any for psycopg2 cursor

# Regex pattern to match ? placeholders (not inside quotes)
_PLACEHOLDER_PATTERN = re.compile(r"\?(?=(?:[^']*'[^']*')*[^']*$)")


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
        logger.info(f"Database initialized with type: {self.type}")

    def _get_conn(self) -> DbConnection:
        """Get or create a thread-local database connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self.connect()
        return self._local.connection

    def connect(self) -> Optional[DbConnection]:
        """Establish a connection to the database for the current thread."""
        with self._lock:
            if self.type == "sqlite":
                conn = self._connect_sqlite()
            elif self.type == "postgres":
                conn = self._connect_postgres()
            else:
                raise ValueError(f"Unsupported database type: {self.type}")

            self._local.connection = conn
            # Initialize thread-local state
            self._local.transaction_depth = 0
            self._local.in_transaction = False
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

            logger.info(f"Connected to SQLite at {path}")
            return conn
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to SQLite: {e}")
            raise

    def _connect_postgres(self) -> DbConnection:
        """Connect to PostgreSQL using thread-safe pooling."""
        try:
            import psycopg2
            import psycopg2.extras
            import psycopg2.pool
        except ImportError:
            raise ImportError("psycopg2-binary is required for PostgreSQL support.")

        if not self._pool:
            pg_config = self.config.get("postgres", {})
            pool_config = self.config.get("connection_pool", {})

            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=pool_config.get("min_connections", 2),
                maxconn=pool_config.get("max_connections", 20),
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
            conn = self._pool.getconn()
            conn.autocommit = False
            logger.info("Connected to PostgreSQL via pool")
            return conn
        except psycopg2.Error as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    def _sanitize_identifier(self, identifier: str) -> str:
        """Sanitize SQL identifiers (table/column names)."""
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier):
            raise ValueError(f"Invalid identifier: {identifier}")
        return f'"{identifier}"' if self.type == "postgres" else f"`{identifier}`"

    def execute(
        self, query: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> DbCursor:
        """Execute a query using the thread-local connection."""
        conn = self._get_conn()

        # Dialect-aware placeholder conversion
        if self.type == "postgres":
            # For a production system, we'd use a real SQL parser.
            # Here we do a safer replacement than the previous global regex.
            query = query.replace("?", "%s")

        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            if auto_commit and not self._local.in_transaction:
                conn.commit()
            return cursor
        except Exception as e:
            logger.error(f"Query failed: {e}")
            # PostgreSQL requires rollback on ANY error before continuing
            if self.type == "postgres":
                if self._local.in_transaction:
                    # If we're in a managed transaction, we must ROLLBACK 
                    # but we also need to inform the transaction manager 
                    # that the transaction is now invalid.
                    # For now, we do a full rollback to be safe.
                    conn.rollback()
                    self._local.in_transaction = False
                    self._local.transaction_depth = 0
                else:
                    conn.rollback()
            elif not self._local.in_transaction:
                conn.rollback()
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
        if self._local.transaction_depth == 0:
            if self.type == "sqlite":
                conn.execute("BEGIN")
            self._local.in_transaction = True

        self._local.transaction_depth += 1
        conn.execute(f"SAVEPOINT sp_{self._local.transaction_depth}")

    def commit(self) -> None:
        """Commit the current transaction level."""
        conn = self._get_conn()
        if self._local.transaction_depth > 0:
            conn.execute(f"RELEASE SAVEPOINT sp_{self._local.transaction_depth}")
            self._local.transaction_depth -= 1

            if self._local.transaction_depth == 0:
                conn.commit()
                self._local.in_transaction = False

    def rollback(self) -> None:
        """Rollback the current transaction level."""
        conn = self._get_conn()
        if self._local.transaction_depth > 0:
            conn.execute(f"ROLLBACK TO SAVEPOINT sp_{self._local.transaction_depth}")
            self._local.transaction_depth -= 1

            if self._local.transaction_depth == 0:
                conn.rollback()
                self._local.in_transaction = False
        else:
            conn.rollback()
            self._local.in_transaction = False

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
        return cursor.rowcount > 0

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

        self.execute(query, values)

    def close(self) -> None:
        """Close the thread-local connection or return to pool."""
        if hasattr(self._local, "connection") and self._local.connection:
            if self._pool:
                self._pool.putconn(self._local.connection)
            else:
                self._local.connection.close()
            self._local.connection = None

    def execute_many(self, query: str, params_list: List[Tuple]) -> DbCursor:
        """Execute batch query safely."""
        conn = self._get_conn()
        if self.type == "postgres":
            query = query.replace("?", "%s")

        cursor = conn.cursor()
        try:
            cursor.executemany(query, params_list)
            if not self._local.in_transaction:
                conn.commit()
            return cursor
        except Exception as e:
            logger.error(f"Batch query failed: {e}")
            if self.type == "postgres":
                if self._local.in_transaction:
                    conn.rollback()
                    self._local.in_transaction = False
                    self._local.transaction_depth = 0
                else:
                    conn.rollback()
            elif not self._local.in_transaction:
                conn.rollback()
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
