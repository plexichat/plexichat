"""Custom status operations mixin.

Handles setting, getting, and clearing user custom status messages
with automatic cleanup of expired statuses.
"""

from typing import Optional

import utils.logger as logger

from .base import PresenceManagerBase
from ..models import Presence, CustomStatus


class CustomStatusMixin(PresenceManagerBase):
    """Mixin providing custom status operations."""

    def _cleanup_expired_custom_status(self, user_id: int) -> None:
        """Remove expired custom status for user."""
        now = self._get_timestamp()
        self._db.execute(
            "DELETE FROM pres_custom_status WHERE user_id = ? AND expires_at IS NOT NULL AND expires_at < ?",
            (user_id, now),
        )

    def set_custom_status(
        self,
        user_id: int,
        text: str,
        emoji: Optional[str] = None,
        expires_at: Optional[int] = None,
    ) -> Presence:
        """
        Set user's custom status message.

        Args:
            user_id: ID of the user
            text: Custom status text
            emoji: Optional emoji
            expires_at: Optional expiration timestamp in milliseconds

        Returns:
            Updated Presence
        """
        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        now = self._get_timestamp()

        if emoji and len(emoji) > 64:
            emoji = emoji[:64]

        self._db.upsert(
            "pres_custom_status",
            ["user_id", "text", "emoji", "expires_at", "created_at"],
            (user_id, text, emoji, expires_at, now),
            ["user_id"],
            ["text", "emoji", "expires_at"],
        )

        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?", (now, user_id)
        )

        logger.debug(f"User {user_id} custom status set")

        result = self.get_presence(user_id)
        return result

    def get_custom_status(self, user_id: int) -> Optional[CustomStatus]:
        """Get user's custom status."""
        self._cleanup_expired_custom_status(user_id)

        row = self._db.fetch_one(
            "SELECT * FROM pres_custom_status WHERE user_id = ?", (user_id,)
        )

        if not row:
            return None

        return CustomStatus(
            text=row["text"],
            emoji=row["emoji"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    def clear_custom_status(self, user_id: int) -> Presence:
        """Clear user's custom status."""
        self._validate_user(user_id)
        self._db.execute("DELETE FROM pres_custom_status WHERE user_id = ?", (user_id,))

        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?",
            (self._get_timestamp(), user_id),
        )

        logger.debug(f"User {user_id} custom status cleared")

        return self.get_presence(user_id)
