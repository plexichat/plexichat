"""
Base repository with common database operations.
"""

from typing import Any, Dict, List, Optional, Tuple, TypeVar, Generic
from abc import ABC
import json

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """Base class for all repositories."""

    def __init__(self, db: Any) -> None:
        """
        Initialize repository with database connection.

        Args:
            db: Database instance (SQLite or PostgreSQL compatible)
        """
        self._db = db

    def _execute(
        self,
        query: str,
        params: Optional[Tuple[Any, ...]] = None,
        auto_commit: bool = True,
    ) -> None:
        """Execute a query without returning results."""
        if hasattr(self._db, "execute"):
            cursor = None
            try:
                if auto_commit:
                    cursor = self._db.execute(query, params or ())
                else:
                    cursor = self._db.execute(query, params or (), auto_commit=False)
            finally:
                if cursor:
                    cursor.close()
        else:
            raise RuntimeError("Database does not support execute")

    def _fetch_one(
        self, query: str, params: Optional[Tuple[Any, ...]] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single row."""
        if hasattr(self._db, "fetch_one"):
            return self._db.fetch_one(query, params or ())
        raise RuntimeError("Database does not support fetch_one")

    def _fetch_all(
        self, query: str, params: Optional[Tuple[Any, ...]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch all rows."""
        if hasattr(self._db, "fetch_all"):
            return self._db.fetch_all(query, params or ())
        raise RuntimeError("Database does not support fetch_all")

    def _build_in_clause(self, items: List[Any]) -> Tuple[str, Tuple[Any, ...]]:
        """Build an IN clause with placeholders."""
        if not items:
            return "(NULL)", ()
        placeholders = ",".join("?" * len(items))
        return f"({placeholders})", tuple(items)

    def _json_dumps(self, data: Optional[Dict[str, Any]]) -> Optional[str]:
        """Serialize dict to JSON string."""
        return json.dumps(data) if data else None

    def _json_loads(self, data: Optional[str]) -> Optional[Dict[str, Any]]:
        """Deserialize JSON string to dict."""
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None

    def begin_transaction(self) -> None:
        """Begin a database transaction."""
        if hasattr(self._db, "begin_transaction"):
            self._db.begin_transaction()

    def commit(self) -> None:
        """Commit the current transaction."""
        if hasattr(self._db, "commit"):
            self._db.commit()

    def rollback(self) -> None:
        """Rollback the current transaction."""
        if hasattr(self._db, "rollback"):
            self._db.rollback()
