"""Typing indicator operations mixin.

Handles starting, stopping, querying, and clearing typing indicators
with Redis SETs for high-speed lookups and database persistence as fallback.
"""

from typing import List

import utils.logger as logger
from src.core.database import (
    redis_available,
    get_redis_client,
)

from .base import PresenceManagerBase
from ..models import TypingIndicator


class TypingMixin(PresenceManagerBase):
    """Mixin providing typing indicator operations."""

    def _cleanup_expired_typing(self) -> None:
        """Remove expired typing indicators."""
        now = self._get_timestamp()
        self._db.execute("DELETE FROM pres_typing WHERE expires_at < ?", (now,))

    def start_typing(self, user_id: int, channel_id: int) -> TypingIndicator:
        """
        Start typing indicator in a channel.

        Args:
            user_id: ID of the user
            channel_id: ID of the channel

        Returns:
            TypingIndicator with started_at and expires_at
        """
        self._validate_user(user_id)

        now = self._get_timestamp()
        expires_at = now + self._typing_timeout_ms

        if redis_available():
            client = get_redis_client()
            if client:
                try:
                    key = f"typing:channel:{channel_id}"
                    client.sadd(key, str(user_id))
                    client.expire(key, self._typing_timeout_ms // 1000)

                    client.sadd(f"typing:user:{user_id}", str(channel_id))
                    client.expire(f"typing:user:{user_id}", 60)
                except Exception as e:
                    logger.debug(f"Redis start_typing failed: {e}")

        self._cleanup_expired_typing()
        indicator_id = self._generate_id()
        self._db.upsert(
            "pres_typing",
            ["id", "user_id", "channel_id", "started_at", "expires_at"],
            (indicator_id, user_id, channel_id, now, expires_at),
            ["user_id", "channel_id"],
            ["id", "started_at", "expires_at"],
        )

        logger.debug(f"User {user_id} started typing in channel {channel_id}")

        return TypingIndicator(
            user_id=user_id,
            channel_id=channel_id,
            started_at=now,
            expires_at=expires_at,
        )

    def stop_typing(self, user_id: int, channel_id: int) -> bool:
        """
        Stop typing indicator in a channel.

        Args:
            user_id: ID of the user
            channel_id: ID of the channel

        Returns:
            True if successful
        """
        if redis_available():
            client = get_redis_client()
            if client:
                try:
                    client.srem(f"typing:channel:{channel_id}", str(user_id))
                    client.srem(f"typing:user:{user_id}", str(channel_id))
                except Exception as e:
                    logger.debug(f"Redis stop_typing failed: {e}")

        self._db.execute(
            "DELETE FROM pres_typing WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        logger.debug(f"User {user_id} stopped typing in channel {channel_id}")

        return True

    def get_typing_users(self, channel_id: int) -> List[TypingIndicator]:
        """
        Get users currently typing in a channel.

        Args:
            channel_id: ID of the channel

        Returns:
            List of TypingIndicator objects
        """
        if redis_available():
            client = get_redis_client()
            if client:
                try:
                    user_ids = client.smembers(f"typing:channel:{channel_id}")
                    if user_ids:
                        now = self._get_timestamp()
                        return [
                            TypingIndicator(
                                user_id=int(uid),
                                channel_id=channel_id,
                                started_at=now - 1000,
                                expires_at=now + 5000,
                            )
                            for uid in user_ids
                        ]
                except Exception as e:
                    logger.debug(f"Redis get_typing_users failed: {e}")

        self._cleanup_expired_typing()
        rows = self._db.fetch_all(
            "SELECT * FROM pres_typing WHERE channel_id = ?", (channel_id,)
        )

        return [
            TypingIndicator(
                user_id=row["user_id"],
                channel_id=row["channel_id"],
                started_at=row["started_at"],
                expires_at=row["expires_at"],
            )
            for row in rows
        ]

    def get_user_typing_channels(self, user_id: int) -> List[int]:
        """
        Get all channels where a user is currently typing.

        Args:
            user_id: ID of the user

        Returns:
            List of channel IDs where user has active typing indicators
        """
        self._cleanup_expired_typing()

        rows = self._db.fetch_all(
            "SELECT channel_id FROM pres_typing WHERE user_id = ?", (user_id,)
        )

        return [row["channel_id"] for row in rows]

    def clear_all_typing(self, user_id: int) -> List[int]:
        """
        Clear all typing indicators for a user (used on disconnect).

        Args:
            user_id: ID of the user

        Returns:
            List of channel IDs that were cleared
        """
        channels = self.get_user_typing_channels(user_id)

        if redis_available():
            client = get_redis_client()
            if client:
                try:
                    for cid in channels:
                        client.srem(f"typing:channel:{cid}", str(user_id))
                    client.delete(f"typing:user:{user_id}")
                except Exception as e:
                    logger.debug(f"Redis clear_all_typing failed: {e}")

        if channels:
            self._db.execute("DELETE FROM pres_typing WHERE user_id = ?", (user_id,))
            logger.debug(
                f"Cleared typing indicators for user {user_id} in {len(channels)} channels"
            )

        return channels
