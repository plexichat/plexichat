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
import sys
import os
import re
from typing import Any, List, Optional, Tuple, Union

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
if common_utils_path not in sys.path:
    sys.path.append(common_utils_path)

import utils.config as config
import utils.logger as logger

# Type alias for database connections
DbConnection = Union[sqlite3.Connection, Any]  # Any for psycopg2 connection

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
            raise ValueError("Database configuration not found. Ensure config is set up.")
        
        self.type = self.config.get("type", "sqlite")
        self.connection: Optional[DbConnection] = None
        self._cursor = None
        self._in_transaction = False
        logger.info(f"Database initialized with type: {self.type}")

    def connect(self):
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

    def _connect_sqlite(self):
        """Connect to SQLite database, creating directories if needed."""
        path = self.config.get("path", "data/database.db")
        
        db_dir = os.path.dirname(path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.debug(f"Created database directory: {db_dir}")
        
        try:
            self.connection = sqlite3.connect(path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            logger.info(f"Connected to SQLite at {path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to SQLite: {e}")
            raise

    def _connect_postgres(self):
        """Connect to PostgreSQL database using psycopg2."""
        try:
            import psycopg2
            import psycopg2.extras
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

        try:
            self.connection = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=dbname,
                cursor_factory=psycopg2.extras.RealDictCursor
            )
            # Set autocommit off by default (matches SQLite behavior)
            self.connection.autocommit = False
            logger.info(f"Connected to PostgreSQL at {host}:{port}/{dbname}")
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

    def _ensure_connected(self):
        """Ensure database is connected before operations."""
        if not self.connection:
            raise ConnectionError("Database not connected. Call connect() first.")

    def execute(self, query: str, params: Optional[Tuple] = None, auto_commit: bool = True):
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

    def execute_many(self, query: str, params_list: List[Tuple]):
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
            logger.debug(f"Executed batch query: {query[:100]}... ({len(params_list)} rows)")
            return cursor
        except Exception as e:
            logger.error(f"Batch query execution failed: {query[:100]}... - {e}")
            self.connection.rollback()
            cursor.close()
            raise

    def fetch_one(self, query: str, params: Optional[Tuple] = None) -> Optional[Any]:
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

    def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[Any]:
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

    def begin_transaction(self):
        """Begin a transaction (disables autocommit)."""
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright
        self._in_transaction = True
        if self.type == "sqlite":
            self.connection.execute("BEGIN")
        logger.debug("Transaction started")

    def commit(self):
        """Commit the current transaction."""
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright
        self.connection.commit()
        self._in_transaction = False
        logger.debug("Transaction committed")

    def rollback(self):
        """Rollback the current transaction."""
        self._ensure_connected()
        assert self.connection is not None  # Type narrowing for pyright
        self.connection.rollback()
        self._in_transaction = False
        logger.debug("Transaction rolled back")

    def close(self):
        """Close the database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info("Database connection closed.")

    def __enter__(self):
        """Context manager entry - connects to database."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes connection."""
        self.close()
        return False
