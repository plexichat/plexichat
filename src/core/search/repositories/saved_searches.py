"""
Repository for saved searches.
"""

from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID


class SavedSearchesRepository:
    """Repository for saved searches operations."""

    def __init__(self, db: Any) -> None:
        """Initialize repository with database instance."""
        self._db = db

    def create(
        self,
        search_id: SnowflakeID,
        user_id: SnowflakeID,
        name: str,
        query: str,
        created_at: int,
    ) -> None:
        """Create a new saved search."""
        self._db.execute(
            """INSERT INTO saved_searches
               (id, user_id, name, query, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (search_id, user_id, name, query, created_at),
        )

    def get(
        self, search_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]:
        """Get a saved search by ID."""
        return self._db.fetch_one(
            """SELECT id, user_id, name, query, created_at
               FROM saved_searches
               WHERE id = ? AND user_id = ?""",
            (search_id, user_id),
        )

    def get_all(self, user_id: SnowflakeID) -> List[Dict[str, Any]]:
        """Get all saved searches for a user."""
        return self._db.fetch_all(
            """SELECT id, user_id, name, query, created_at
               FROM saved_searches
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )

    def update(
        self,
        search_id: SnowflakeID,
        user_id: SnowflakeID,
        name: Optional[str] = None,
        query: Optional[str] = None,
    ) -> bool:
        """Update a saved search."""
        if name is None and query is None:
            return False

        if name:
            self._db.execute(
                """UPDATE saved_searches
                   SET name = ?
                   WHERE id = ? AND user_id = ?""",
                (name, search_id, user_id),
            )

        if query:
            self._db.execute(
                """UPDATE saved_searches
                   SET query = ?
                   WHERE id = ? AND user_id = ?""",
                (query, search_id, user_id),
            )

        return True

    def delete(self, search_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        """Delete a saved search."""
        self._db.execute(
            "DELETE FROM saved_searches WHERE id = ? AND user_id = ?",
            (search_id, user_id),
        )
        return True

    def exists(self, search_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        """Check if a saved search exists."""
        row = self._db.fetch_one(
            "SELECT 1 FROM saved_searches WHERE id = ? AND user_id = ?",
            (search_id, user_id),
        )
        return row is not None

    def count(self, user_id: SnowflakeID) -> int:
        """Count saved searches for a user."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM saved_searches WHERE user_id = ?",
            (user_id,),
        )
        return row["count"] if row else 0
