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

    Usage:
        db = Database()
        db.connect()

        # Execute queries
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO users (name) VALUES (?)", ("Alice",))

        # Fetch data
        rows = db.fetch_all("SELECT * FROM users")
        row = db.fetch_one("SELECT * FROM users WHERE id = ?", (1,))

        db.close()
    """

    def __init__(self):
        """Initialize the database manager with configuration."""
        self.config = config.get("database")
        if not self.config:
            raise ValueError(
                "Database configuration not found. Ensure config is set up."
            )

        self.type = self.config.get("type", "sqlite")
        self.connection: Optional[DbConnection] = None
        self._pool = None  # PostgreSQL connection pool
        self._cursor = None
        self._in_transaction = False
        logger.info(f"Database initialized with type: {self.type}")

    def connect(self) -> None:
        """
        Establish a connection to the database.

        Raises:
            ValueError: If database type is not supported.
            sqlite3.Error: If SQLite connection fails.
            psycopg2.Error: If PostgreSQL connection fails.
        """
        logger.info(f"Connecting to {self.type} database...")

        if self.type == "sqlite":
            self._connect_sqlite()
        elif self.type == "postgres":
            self._connect_postgres()
        else:
            error_msg = f"Unsupported database type: {self.type}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def _connect_sqlite(self) -> None:
        """Connect to SQLite database, creating directories if needed."""
        path = self.config.get("path", "data/database.db")

        db_dir = os.path.dirname(path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.debug(f"Created database directory: {db_dir}")

        try:
            self.connection = sqlite3.connect(path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row

            # Enable WAL mode for better concurrent read/write performance
            self.connection.execute("PRAGMA journal_mode=WAL")
            # Set synchronous to NORMAL for better performance (still safe with WAL)
            self.connection.execute("PRAGMA synchronous=NORMAL")
            # Increase cache size (negative = KB, so -64000 = 64MB)
            self.connection.execute("PRAGMA cache_size=-64000")
            # Enable memory-mapped I/O for faster reads (256MB)
            self.connection.execute("PRAGMA mmap_size=268435456")
            # Store temp tables in memory
            self.connection.execute("PRAGMA temp_store=MEMORY")
            # Enable foreign keys
            self.connection.execute("PRAGMA foreign_keys=ON")

            logger.info(f"Connected to SQLite at {path} (WAL mode enabled)")

            # Run migrations
            from .migrations import run_all_migrations

            run_all_migrations(self)
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to SQLite: {e}")
            raise

    def _connect_postgres(self) -> None:
        """Connect to PostgreSQL database using psycopg2 with connection pooling."""
        try:
            import psycopg2
            import psycopg2.extras
            import psycopg2.pool
        except ImportError:
            logger.error("psycopg2 is not installed. Cannot connect to PostgreSQL.")
            raise ImportError(
                "psycopg2 is required for PostgreSQL support. "
                "Install with: pip install psycopg2-binary"
            )

        pg_config = self.config.get("postgres", {})
        host = pg_config.get("host", "localhost")
        port = pg_config.get("port", 5432)
        user = pg_config.get("user", "postgres")
        password = pg_config.get("password", "")
        dbname = pg_config.get("dbname", "plexichat")
        sslmode = pg_config.get("sslmode", "prefer")

        # Connection pool settings
        pool_config = self.config.get("connection_pool", {})
        min_conn = pool_config.get("min_connections", 2)
        max_conn = pool_config.get("max_connections", 20)

        try:
            # Use ThreadedConnectionPool for thread-safe connection pooling
            # Set synchronous_commit=off for faster commits (data is still durable, just async)
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=min_conn,
                maxconn=max_conn,
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=dbname,
                sslmode=sslmode,
                cursor_factory=psycopg2.extras.RealDictCursor,
                options="-c client_encoding=UTF8 -c synchronous_commit=off",
            )
            # Get initial connection from pool
            conn = self._pool.getconn()
            self.connection = conn
            # Set autocommit off by default (matches SQLite behavior)
            conn.autocommit = False
            logger.info(
                f"Connected to PostgreSQL at {host}:{port}/{dbname} (sslmode={sslmode}, pool={min_conn}-{max_conn})"
            )

            # Run migrations
            from .migrations import run_all_migrations

            run_all_migrations(self)
        except psycopg2.Error as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    def _convert_placeholders(self, query: str) -> str:
        """
        Convert SQLite-style ? placeholders to PostgreSQL-style %s.

        This allows using the same query syntax for both databases.
        Only converts ? that are not inside quoted strings.

        Args:
            query: SQL query with ? placeholders.

        Returns:
            Query with %s placeholders if PostgreSQL, unchanged if SQLite.
        """
        if self.type != "postgres":
            return query
        return _PLACEHOLDER_PATTERN.sub("%s", query)

    def convert_schema(self, schema: str) -> str:
        """
        Convert SQLite schema to PostgreSQL-compatible schema.

        Handles type conversions:
        - BLOB -> BYTEA (binary data)
        - INTEGER -> BIGINT (for snowflake IDs which exceed 32-bit range)
        - AUTOINCREMENT -> (removed, PostgreSQL uses SERIAL or BIGSERIAL)

        Args:
            schema: SQL schema string with SQLite types.

        Returns:
            Schema with PostgreSQL-compatible types if PostgreSQL, unchanged if SQLite.
        """
        if self.type != "postgres":
            return schema

        # Convert SQLite types to PostgreSQL equivalents
        converted = schema
        converted = re.sub(r"\bBLOB\b", "BYTEA", converted, flags=re.IGNORECASE)
        # Convert all INTEGER to BIGINT (snowflake IDs exceed 32-bit INTEGER range)
        converted = re.sub(r"\bINTEGER\b", "BIGINT", converted, flags=re.IGNORECASE)

        return converted

    def _ensure_connected(self) -> None:
        """Ensure database is connected before operations."""
        if not self.connection:
            raise ConnectionError("Database not connected. Call connect() first.")

    def execute(
        self, query: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> DbCursor:
        """
        Execute a query and return the cursor.

        Args:
            query: SQL query string. Use ? for placeholders (auto-converted to %s for PostgreSQL).
            params: Optional tuple of parameters for parameterized queries.
            auto_commit: Whether to auto-commit after execution (default True).
                        Set to False when using transactions.

        Returns:
            Database cursor after execution.

        Raises:
            ConnectionError: If not connected to database.
            sqlite3.Error/psycopg2.Error: If query execution fails.
        """
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright

        # Convert ? to %s for PostgreSQL
        converted_query = self._convert_placeholders(query)

        cursor = self.connection.cursor()
        try:
            if params:
                cursor.execute(converted_query, params)
            else:
                cursor.execute(converted_query)
            if auto_commit and not self._in_transaction:
                self.connection.commit()
            logger.debug(f"Executed query: {query[:100]}...")
            return cursor
        except Exception as e:
            logger.error(f"Query execution failed: {query[:100]}... - {e}")
            self.connection.rollback()
            cursor.close()
            raise

    def execute_many(self, query: str, params_list: List[Tuple]) -> DbCursor:
        """
        Execute a query multiple times with different parameters.

        Args:
            query: SQL query string. Use ? for placeholders (auto-converted to %s for PostgreSQL).
            params_list: List of parameter tuples.

        Returns:
            Database cursor after execution.
        """
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright

        # Convert ? to %s for PostgreSQL
        converted_query = self._convert_placeholders(query)

        cursor = self.connection.cursor()
        try:
            cursor.executemany(converted_query, params_list)
            self.connection.commit()
            logger.debug(
                f"Executed batch query: {query[:100]}... ({len(params_list)} rows)"
            )
            return cursor
        except Exception as e:
            logger.error(f"Batch query execution failed: {query[:100]}... - {e}")
            self.connection.rollback()
            cursor.close()
            raise

    def fetch_one(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Union[sqlite3.Row, Dict[str, Any]]]:
        """
        Execute a query and fetch one result.

        Args:
            query: SQL query string.
            params: Optional tuple of parameters.

        Returns:
            Single row result or None if no results.
        """
        cursor = self.execute(query, params)
        result = cursor.fetchone()
        cursor.close()
        return result

    def fetch_all(
        self, query: str, params: Optional[Tuple] = None
    ) -> List[Union[sqlite3.Row, Dict[str, Any]]]:
        """
        Execute a query and fetch all results.

        Args:
            query: SQL query string.
            params: Optional tuple of parameters.

        Returns:
            List of all row results.
        """
        cursor = self.execute(query, params)
        results = cursor.fetchall()
        cursor.close()
        return results

    def table_exists(self, table_name: str) -> bool:
        """
        Check if a table exists in the database.

        Args:
            table_name: Name of the table to check.

        Returns:
            True if table exists, False otherwise.
        """
        self._ensure_connected()

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
        """Begin a transaction (disables autocommit)."""
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright
        self._in_transaction = True
        if self.type == "sqlite":
            self.connection.execute("BEGIN")
        logger.debug("Transaction started")

    def commit(self) -> None:
        """Commit the current transaction."""
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright
        self.connection.commit()
        self._in_transaction = False
        logger.debug("Transaction committed")

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright
        self.connection.rollback()
        self._in_transaction = False
        logger.debug("Transaction rolled back")

    def insert_or_ignore(self, table: str, columns: List[str], values: Tuple) -> bool:
        """
        Insert a row if it doesn't already exist (based on primary key/unique constraint).

        Cross-database compatible alternative to SQLite's INSERT OR IGNORE.

        Args:
            table: Table name.
            columns: List of column names.
            values: Tuple of values corresponding to columns.

        Returns:
            True if row was inserted, False if ignored due to conflict.
        """
        self._ensure_connected()

        placeholders = ", ".join(["?"] * len(columns))
        cols = ", ".join(columns)

        if self.type == "sqlite":
            query = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
        elif self.type == "postgres":
            query = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        else:
            raise ValueError(f"Unsupported database type: {self.type}")

        cursor = self.execute(query, values)
        inserted = cursor.rowcount > 0
        cursor.close()
        return inserted

    def upsert(
        self,
        table: str,
        columns: List[str],
        values: Tuple,
        conflict_columns: List[str],
        update_columns: Optional[List[str]] = None,
    ) -> None:
        """
        Insert a row or update it if it already exists.

        Cross-database compatible alternative to SQLite's INSERT OR REPLACE.

        Args:
            table: Table name.
            columns: List of column names for insert.
            values: Tuple of values corresponding to columns.
            conflict_columns: Columns that define uniqueness (for ON CONFLICT).
            update_columns: Columns to update on conflict. If None, updates all non-conflict columns.
        """
        self._ensure_connected()

        placeholders = ", ".join(["?"] * len(columns))
        cols = ", ".join(columns)

        if update_columns is None:
            update_columns = [c for c in columns if c not in conflict_columns]

        if self.type == "sqlite":
            query = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
        elif self.type == "postgres":
            conflict_cols = ", ".join(conflict_columns)
            if update_columns:
                updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_columns])
                query = f"""INSERT INTO {table} ({cols}) VALUES ({placeholders})
                           ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"""
            else:
                query = f"""INSERT INTO {table} ({cols}) VALUES ({placeholders})
                           ON CONFLICT ({conflict_cols}) DO NOTHING"""
        else:
            raise ValueError(f"Unsupported database type: {self.type}")

        self.execute(query, values)

    def close(self) -> None:
        """Close the database connection and pool."""
        if self.connection:
            if self._pool:
                # Return connection to pool and close pool
                self._pool.putconn(self.connection)
                self._pool.closeall()
                self._pool = None
            else:
                self.connection.close()
            self.connection = None
            logger.info("Database connection closed.")

    def __enter__(self) -> "Database":
        """Context manager entry - connects to database."""
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Context manager exit - closes connection."""
        self.close()
        return False
