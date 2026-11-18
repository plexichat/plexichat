"""Custom status operations mixin.

Handles setting, getting, and clearing user custom status messages
with automatic cleanup of expired statuses.
"""

from typing import Optional

import utils.logger as logger

from src.core.security.hardening import HardeningViolation, bounded_int

from .base import PresenceManagerBase
from ..exceptions import InvalidStatusError
from ..models import Presence, CustomStatus
from ..validation import MAX_STATUS_TEXT_CHARS, validate_emoji, validate_status_text

# Custom-status expiry is an epoch-millisecond value persisted as a DB integer
# and later coerced with ``int()`` on the read path. Refuse anything outside a
# signed 64-bit range (and non-integers) so a hostile value cannot overflow the
# column or crash presence reads with a ValueError.
_MAX_EXPIRES_AT = 2**63 - 1


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

        # Normalize inputs. A custom status may be emoji-only: an empty /
        # whitespace-only ``text`` is only treated as "clear" when there is no
        # emoji either. With an emoji present we keep an emoji-only status
        # (storing an empty ``text``), which previously was silently dropped.
        max_len = bounded_int(
            self._config.get("max_custom_status_length", 128),
            "max_custom_status_length",
            minimum=1,
            maximum=MAX_STATUS_TEXT_CHARS,
        )
        normalized_text = validate_status_text(text, max_len)
        emoji = validate_emoji(emoji)

        if expires_at is not None:
            try:
                expires_at = bounded_int(
                    expires_at,
                    "expires_at",
                    minimum=0,
                    maximum=_MAX_EXPIRES_AT,
                )
            except HardeningViolation as exc:
                raise InvalidStatusError("Invalid custom status expiry") from exc

        if not normalized_text and not emoji:
            return self.clear_custom_status(user_id)

        now = self._get_timestamp()

        self._db.upsert(
            "pres_custom_status",
            ["user_id", "text", "emoji", "expires_at", "created_at"],
            (user_id, normalized_text, emoji, expires_at, now),
            ["user_id"],
            ["text", "emoji", "expires_at"],
        )

        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?", (now, user_id)
        )

        logger.debug(f"User {user_id} custom status set")

        return self._reseed_presence_cache(user_id)

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

        return self._reseed_presence_cache(user_id)
