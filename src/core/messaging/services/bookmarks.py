"""
User bookmarks service - Business logic for per-user message bookmarks.

Allows users to bookmark/pin messages for their own reference,
independently of server/channel pins.
"""

import time
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


class BookmarkService:
    """Service for managing per-user message bookmarks."""

    MAX_BOOKMARKS_PER_USER = 200
    MAX_LABEL_LENGTH = 100

    def __init__(self, db, messaging_module=None):
        self._db = db
        self._messaging = messaging_module

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def add_bookmark(
        self,
        user_id: int,
        message_id: int,
        conversation_id: int,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Bookmark a message for a user.

        Args:
            user_id: ID of the user bookmarking
            message_id: ID of the message to bookmark
            conversation_id: ID of the conversation the message is in
            label: Optional label for the bookmark

        Returns:
            Bookmark record dict
        """
        # Check limit
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM user_bookmarks WHERE user_id = ?",
            (user_id,),
        )
        count = count_row["count"] if count_row else 0
        if count >= self.MAX_BOOKMARKS_PER_USER:
            raise ValueError(
                f"Maximum {self.MAX_BOOKMARKS_PER_USER} bookmarks per user"
            )

        # Validate label
        if label:
            label = label.strip()[: self.MAX_LABEL_LENGTH]

        # Check if already bookmarked
        existing = self._db.fetch_one(
            "SELECT id FROM user_bookmarks WHERE user_id = ? AND message_id = ?",
            (user_id, message_id),
        )
        if existing:
            # Update label if provided
            if label is not None:
                self._db.execute(
                    "UPDATE user_bookmarks SET label = ? WHERE id = ?",
                    (label, existing["id"]),
                )
            row = self._db.fetch_one(
                "SELECT * FROM user_bookmarks WHERE id = ?", (existing["id"],)
            )
            return dict(row) if row else {}

        now = self._get_timestamp()
        bookmark_id = self._generate_id()

        self._db.execute(
            """INSERT INTO user_bookmarks (id, user_id, message_id, conversation_id, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bookmark_id, user_id, message_id, conversation_id, label, now),
        )

        logger.debug(f"User {user_id} bookmarked message {message_id}")
        row = self._db.fetch_one(
            "SELECT * FROM user_bookmarks WHERE id = ?", (bookmark_id,)
        )
        return dict(row) if row else {}

    def remove_bookmark(self, user_id: int, message_id: int) -> bool:
        """Remove a bookmark for a user."""
        self._db.execute(
            "DELETE FROM user_bookmarks WHERE user_id = ? AND message_id = ?",
            (user_id, message_id),
        )
        return True

    def get_bookmarks(
        self,
        user_id: int,
        conversation_id: Optional[int] = None,
        limit: int = 50,
        before_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get bookmarks for a user, optionally filtered by conversation."""
        query = "SELECT * FROM user_bookmarks WHERE user_id = ?"
        params: list = [user_id]

        if conversation_id:
            query += " AND conversation_id = ?"
            params.append(conversation_id)

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))

        # Enrich with message content if messaging module available
        results = []
        for row in rows:
            data = dict(row)
            if self._messaging:
                try:
                    msg = self._messaging._message_svc.get_message_raw(
                        data["message_id"]
                    )
                    if msg:
                        data["content_preview"] = (msg.get("content", "") or "")[:200]
                        data["author_id"] = msg.get("author_id")
                        data["message_created_at"] = msg.get("created_at")
                except Exception:
                    pass
            results.append(data)
        return results

    def get_bookmark_count(self, user_id: int) -> int:
        """Get the number of bookmarks for a user."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM user_bookmarks WHERE user_id = ?",
            (user_id,),
        )
        return row["count"] if row else 0

    def is_bookmarked(self, user_id: int, message_id: int) -> bool:
        """Check if a message is bookmarked by a user."""
        row = self._db.fetch_one(
            "SELECT 1 FROM user_bookmarks WHERE user_id = ? AND message_id = ?",
            (user_id, message_id),
        )
        return row is not None
