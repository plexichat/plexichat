"""Typing indicator operations mixin.

Handles starting, stopping, querying, and clearing typing indicators
with Valkey SETs for high-speed lookups and database persistence as fallback.
"""

from typing import List, Tuple

import utils.logger as logger
from src.core.database import (
    valkey_available,
    get_valkey_client,
)

from .base import PresenceManagerBase
from ..models import TypingIndicator
from ..typing_coalesce import TypingWriteCoalescer

# A typing burst re-sends TYPING_START for the same (user, channel) many times
# within a second (clients repeat while typing, and REST + WS can both fire).
# Each frame used to issue two DB writes (a global expiry DELETE and a row
# UPSERT). Coalesce those into one write per short window. The tracker is
# bounded and only consulted when Valkey confirms the live state — see
# ``start_typing`` for the correctness argument.
_typing_write_coalescer = TypingWriteCoalescer()


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
        coalesce_key: Tuple[int, int] = (int(user_id), int(channel_id))

        # Refresh the live typing state in Valkey on EVERY frame, coalesced or
        # not. It is cheap and keeps ``get_typing_users`` correct for the whole
        # burst; only the DB writes below are eligible for coalescing.
        valkey_ok = False
        if valkey_available():
            client = get_valkey_client()
            if client:
                try:
                    key = f"typing:channel:{channel_id}"
                    client.sadd(key, str(user_id))
                    client.expire(key, self._typing_timeout_ms // 1000)

                    client.sadd(f"typing:user:{user_id}", str(channel_id))
                    client.expire(f"typing:user:{user_id}", 60)
                    valkey_ok = True
                except Exception as e:
                    logger.debug(f"Valkey start_typing failed: {e}")

        # CORRECTNESS: skip the redundant DB writes only when the authoritative
        # live state is confirmed in Valkey. Without Valkey the DB row is the
        # sole source of truth (and the expiry fallback), so every frame must
        # still write it and refresh its expires_at.
        claim = _typing_write_coalescer.claim(coalesce_key, coalesce=valkey_ok)
        if claim.write:
            self._cleanup_expired_typing()
            indicator_id = self._generate_id()
            self._db.upsert(
                "pres_typing",
                ["id", "user_id", "channel_id", "started_at", "expires_at"],
                (indicator_id, user_id, channel_id, now, expires_at),
                ["user_id", "channel_id"],
                ["id", "started_at", "expires_at"],
            )
            _typing_write_coalescer.record(coalesce_key, now, expires_at)
            started_at, persisted_expires = now, expires_at
        else:
            # Return the timestamps actually persisted by the last real write
            # instead of fabricating an expiry that was never stored.
            started_at = claim.started_at or now
            persisted_expires = claim.expires_at or expires_at

        logger.debug(f"User {user_id} started typing in channel {channel_id}")

        return TypingIndicator(
            user_id=user_id,
            channel_id=channel_id,
            started_at=started_at,
            expires_at=persisted_expires,
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
        if valkey_available():
            client = get_valkey_client()
            if client:
                try:
                    client.srem(f"typing:channel:{channel_id}", str(user_id))
                    client.srem(f"typing:user:{user_id}", str(channel_id))
                except Exception as e:
                    logger.debug(f"Valkey stop_typing failed: {e}")

        self._db.execute(
            "DELETE FROM pres_typing WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        # A stop must drop any live write claim so a start→stop→start burst
        # cannot skip re-inserting the DB row that stop just removed.
        _typing_write_coalescer.invalidate(key=(int(user_id), int(channel_id)))

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
        if valkey_available():
            client = get_valkey_client()
            if client:
                try:
                    user_ids = client.smembers(f"typing:channel:{channel_id}")
                    if user_ids:
                        ids = [int(uid) for uid in user_ids]
                        # CORRECTNESS FIX: the Valkey set only stores member
                        # ids, not timestamps. Fetch the real started_at /
                        # expires_at rows from the DB (written by
                        # ``start_typing``) instead of fabricating values.
                        placeholders = ",".join("?" * len(ids))
                        rows = self._db.fetch_all(
                            f"""SELECT * FROM pres_typing
                                WHERE channel_id = ? AND user_id IN ({placeholders})""",
                            (channel_id,) + tuple(ids),
                        )
                        if rows:
                            return [
                                TypingIndicator(
                                    user_id=row["user_id"],
                                    channel_id=row["channel_id"],
                                    started_at=row["started_at"],
                                    expires_at=row["expires_at"],
                                )
                                for row in rows
                            ]
                except Exception as e:
                    logger.debug(f"Valkey get_typing_users failed: {e}")

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

        if valkey_available():
            client = get_valkey_client()
            if client:
                try:
                    for cid in channels:
                        client.srem(f"typing:channel:{cid}", str(user_id))
                    client.delete(f"typing:user:{user_id}")
                except Exception as e:
                    logger.debug(f"Valkey clear_all_typing failed: {e}")

        # Drop every claim for this user on disconnect so a reconnect burst
        # always re-establishes the backing DB rows.
        _typing_write_coalescer.invalidate(user_id=int(user_id))

        if channels:
            self._db.execute("DELETE FROM pres_typing WHERE user_id = ?", (user_id,))
            logger.debug(
                f"Cleared typing indicators for user {user_id} in {len(channels)} channels"
            )

        return channels
