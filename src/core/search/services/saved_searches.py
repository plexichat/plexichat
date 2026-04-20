"""
Saved searches service - Business logic for saved search queries.
"""

from typing import Optional, List, Dict, Any
import time

from src.core.base import SnowflakeID
import utils.logger as logger


class SavedSearchesService:
    """Service for saved searches operations."""

    def __init__(self, db: Any) -> None:
        """Initialize saved searches service."""
        from .repositories.saved_searches import SavedSearchesRepository  # type: ignore

        self._db = db
        self._repo = SavedSearchesRepository(db)

    def create_search(
        self,
        user_id: SnowflakeID,
        name: str,
        query: str,
    ) -> Dict[str, Any]:
        """
        Create a new saved search.

        Args:
            user_id: ID of the user
            name: Name for the saved search
            query: Search query string

        Returns:
            Created saved search data

        Raises:
            ValueError: If name is empty or query is empty
        """
        if not name or not name.strip():
            raise ValueError("Name cannot be empty")

        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        if len(name) > 100:
            raise ValueError("Name cannot exceed 100 characters")

        if len(query) > 500:
            raise ValueError("Query cannot exceed 500 characters")

        # Check for limit (max 50 saved searches per user)
        count = self._repo.count(user_id)
        if count >= 50:
            raise ValueError("Maximum saved searches limit reached (50)")

        now = int(time.time() * 1000)
        search_id = self._generate_id()

        self._repo.create(
            search_id=search_id,
            user_id=user_id,
            name=name.strip(),
            query=query.strip(),
            created_at=now,
        )

        logger.debug(f"User {user_id} created saved search '{name}'")
        search = self.get_search(search_id, user_id)
        if search is None:
            raise RuntimeError("Failed to retrieve created search")
        return search

    def get_search(
        self,
        search_id: SnowflakeID,
        user_id: SnowflakeID,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a saved search by ID.

        Args:
            search_id: ID of the saved search
            user_id: ID of the user

        Returns:
            Saved search data, or None if not found
        """
        return self._repo.get(search_id, user_id)

    def get_all_searches(self, user_id: SnowflakeID) -> List[Dict[str, Any]]:
        """
        Get all saved searches for a user.

        Args:
            user_id: ID of the user

        Returns:
            List of saved search data
        """
        return self._repo.get_all(user_id)

    def update_search(
        self,
        search_id: SnowflakeID,
        user_id: SnowflakeID,
        name: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update a saved search.

        Args:
            search_id: ID of the saved search
            user_id: ID of the user
            name: New name (optional)
            query: New query (optional)

        Returns:
            Updated saved search data

        Raises:
            ValueError: If neither name nor query provided, or if invalid
        """
        if name is None and query is None:
            raise ValueError("Either name or query must be provided")

        if name is not None:
            if not name or not name.strip():
                raise ValueError("Name cannot be empty")
            if len(name) > 100:
                raise ValueError("Name cannot exceed 100 characters")

        if query is not None:
            if not query or not query.strip():
                raise ValueError("Query cannot be empty")
            if len(query) > 500:
                raise ValueError("Query cannot exceed 500 characters")

        # Check if search exists
        existing = self._repo.get(search_id, user_id)
        if not existing:
            raise ValueError("Saved search not found")

        self._repo.update(search_id, user_id, name, query)

        logger.debug(f"User {user_id} updated saved search {search_id}")
        search = self.get_search(search_id, user_id)
        if search is None:
            raise RuntimeError("Failed to retrieve updated search")
        return search

    def delete_search(
        self,
        search_id: SnowflakeID,
        user_id: SnowflakeID,
    ) -> bool:
        """
        Delete a saved search.

        Args:
            search_id: ID of the saved search
            user_id: ID of the user

        Returns:
            True if deleted
        """
        result = self._repo.delete(search_id, user_id)
        logger.debug(f"User {user_id} deleted saved search {search_id}")
        return result

    def _generate_id(self) -> SnowflakeID:
        """Generate a new Snowflake ID."""
        from src.utils.encryption import generate_snowflake_id

        return generate_snowflake_id()
