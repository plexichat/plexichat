import sqlite3
import os
from typing import Any, Optional, Dict, List, Tuple
from .base import BaseEngine


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
        return {
            "active_connections": 0,
            "idle_connections": 0,
            "total_connections": 0,
            "max_connections": 0,
            "min_connections": 0,
            "utilization_percent": 0,
        }

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
        return f"INSERT INTO {table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"  # nosec B608

