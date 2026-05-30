"""Status operations mixin.

Handles setting, getting, and clearing user online status
with Redis caching for high-speed lookups.
"""

from typing import Union

import utils.logger as logger
from src.core.database import (
    cache_set,
    redis_available,
    get_redis_client,
)

from .base import PresenceManagerBase
from ..models import Presence, UserStatus
from ..exceptions import InvalidStatusError


class StatusMixin(PresenceManagerBase):
    """Mixin providing online status operations."""

    def _update_redis_presence(self, user_id: int, status: str) -> None:
        """Update online users set in Redis for high-speed lookups."""
        if not redis_available():
            return

        client = get_redis_client()
        if not client:
            return

        try:
            key = "presence:online_users"
            if status in ("online", "idle", "dnd"):
                client.sadd(key, str(user_id))
            else:
                client.srem(key, str(user_id))
        except Exception as e:
            logger.debug(f"Failed to update online users set: {e}")

    def set_status(self, user_id: int, status: Union[UserStatus, str]) -> Presence:
        """
        Set user's status.

        Args:
            user_id: ID of the user
            status: New status (online, idle, dnd, offline)

        Returns:
            Updated Presence
        """
        self._validate_user(user_id)

        if isinstance(status, str):
            try:
                status = UserStatus(status.lower())
            except ValueError:
                raise InvalidStatusError(f"Invalid status value: {status}") from None
        elif not isinstance(status, UserStatus):
            raise InvalidStatusError(f"Invalid status type: {type(status).__name__}")

        now = self._get_timestamp()

        result = self._db.execute(
            "UPDATE pres_presence SET status = ?, last_seen = ?, updated_at = ? WHERE user_id = ?",
            (status.value, now, now, user_id),
        )

        if result.rowcount == 0:
            self._ensure_presence_record(user_id)
            self._db.execute(
                "UPDATE pres_presence SET status = ?, last_seen = ?, updated_at = ? WHERE user_id = ?",
                (status.value, now, now, user_id),
            )

        presence = self.get_presence(user_id, use_cache=False)
        if redis_available():
            cache_set(
                f"presence:{user_id}",
                self._presence_to_dict(presence),
                ttl=self._presence_timeout_ms // 1000,
            )
            self._update_redis_presence(user_id, status.value)

        logger.debug(f"User {user_id} status set to {status.value}")
        return presence

    def get_status(self, user_id: int) -> UserStatus:
        """Get user's current status."""
        row = self._db.fetch_one(
            "SELECT status FROM pres_presence WHERE user_id = ?", (user_id,)
        )

        if not row:
            return UserStatus.OFFLINE

        return UserStatus(row["status"])

    def clear_status(self, user_id: int) -> Presence:
        """Clear user's status (set to offline)."""
        return self.set_status(user_id, UserStatus.OFFLINE)
