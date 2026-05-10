import sqlite3
import os
from typing import Any, Optional, Dict, List, Tuple
from .base import BaseEngine

import utils.logger as logger


class SqliteEngine(BaseEngine):
    """SQLite database engine implementation."""

    def connect(self, pool: Optional[Any] = None) -> sqlite3.Connection:
        # SQLite doesn't use the pool parameter here but we accept it for signature compatibility
        db_path = self.config.get("path", "data/database.db")

        # Ensure directory exists
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

        # Enable WAL (Write-Ahead Logging) for concurrency
        conn.execute("PRAGMA journal_mode=WAL;")

        # Recommended for WAL mode: allow readers during writes with minimal sync cost
        conn.execute("PRAGMA synchronous=NORMAL;")

        # Set busy timeout to wait for locks
        conn.execute("PRAGMA busy_timeout=30000;")

        # Enforce foreign keys (disabled by default in SQLite)
        conn.execute("PRAGMA foreign_keys=ON;")

        return conn

    def get_pool_stats(self, pool: Any) -> Dict[str, Any]:
        """Get SQLite-specific database statistics."""
        stats = {
            "active_connections": 0,
            "idle_connections": 0,
            "total_connections": 0,
            "max_connections": 0,
            "min_connections": 0,
            "utilization_percent": 0,
            "database_type": "sqlite",
        }

        # Try to get SQLite-specific metrics if connection is available
        try:
            # Note: SQLite doesn't have connection pooling like PostgreSQL
            # These metrics are placeholders for monitoring consistency
            stats["database_type"] = "sqlite"
            stats["database_path"] = self.config.get("path", "data/database.db")
        except Exception as e:
            logger.debug(f"Could not retrieve SQLite stats: {e}")

        return stats

    def get_detailed_stats(
        self, conn: Optional[sqlite3.Connection] = None
    ) -> Dict[str, Any]:
        """Get detailed SQLite database statistics for monitoring."""
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
            # Get page size
            cursor = conn.cursor()
            cursor.execute("PRAGMA page_size;")
            stats["page_size"] = cursor.fetchone()[0] or 0

            # Get page count
            cursor.execute("PRAGMA page_count;")
            stats["page_count"] = cursor.fetchone()[0] or 0

            # Calculate database size
            stats["database_size_bytes"] = stats["page_size"] * stats["page_count"]

            # Get WAL file size (if WAL mode is enabled)
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
        if conn:
            conn.close()

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
        update_columns: List[str],
    ) -> str:
        # SQLite uses the same syntax as Postgres for modern versions (ON CONFLICT)
        # but Database class implementation for SQLite used ON CONFLICT as well
        # actually looking at core.py:1221, SQLite used INSERT OR IGNORE
        # and 1260, upsert used ON CONFLICT.
        safe_cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        conflict_cols = ", ".join(conflict_columns)
        updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_columns])
        return f"INSERT INTO {table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"
