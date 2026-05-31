"""
Embed attachment mixin - Message-embed association operations.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.embeds.exceptions import (
    EmbedLimitError,
    EmbedNotFoundError,
    MessageNotFoundError,
    PermissionDeniedError,
)
from src.core.embeds.models import Embed
from src.core.embeds.validator import MAX_EMBEDS_PER_MESSAGE
from .protocol import EmbedManagerProtocol


class EmbedAttachmentMixin(EmbedManagerProtocol):
    """
    Mixin providing message-embed attachment operations.

    Depends on:
    - get_embed from EmbedManagerBase
    - _get_message from EmbedManagerBase
    - _is_participant from EmbedManagerBase
    - _get_channel_for_conversation from EmbedManagerBase
    - _check_embed_links_permission from EmbedManagerBase
    - _get_timestamp from BaseManager
    - _generate_id from BaseManager
    """

    _db: Any
    _config: Dict[str, Any]

    def attach_embed_to_message(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        embed_id: SnowflakeID,
        position: Optional[int] = None,
    ) -> bool:
        """
        Attach an embed to a message.

        Args:
            user_id: ID of user attaching embed
            message_id: ID of message
            embed_id: ID of embed to attach
            position: Optional position (0-indexed)

        Returns:
            True if attached

        Raises:
            MessageNotFoundError: Message not found
            EmbedNotFoundError: Embed not found
            EmbedLimitError: Max embeds reached
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only attach embeds to own messages")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        channel = self._get_channel_for_conversation(msg["conversation_id"])
        if channel:
            if not self._check_embed_links_permission(
                user_id, channel["server_id"], channel["id"]
            ):
                raise PermissionDeniedError(
                    "Missing permission to embed links", "messages.embed_links"
                )

        embed = self.get_embed(embed_id)
        if not embed:
            raise EmbedNotFoundError("Embed not found")

        max_embeds = self._config.get("max_embeds_per_message", MAX_EMBEDS_PER_MESSAGE)
        current_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM embed_message_embeds WHERE message_id = ?",
            (message_id,),
        )
        count = current_count["count"] if current_count else 0

        if count >= max_embeds:
            raise EmbedLimitError(
                f"Message has reached maximum of {max_embeds} embeds", max_embeds, count
            )

        existing = self._db.fetch_one(
            "SELECT 1 FROM embed_message_embeds WHERE message_id = ? AND embed_id = ?",
            (message_id, embed_id),
        )
        if existing:
            return True

        if position is None:
            position = count
        else:
            self._db.execute(
                """UPDATE embed_message_embeds
                   SET position = position + 1
                   WHERE message_id = ? AND position >= ?""",
                (message_id, position),
            )

        now = self._get_timestamp()
        assoc_id = self._generate_id()

        self._db.execute(
            """INSERT INTO embed_message_embeds (id, message_id, embed_id, position, suppressed, created_at)
               VALUES (?, ?, ?, ?, 0, ?)""",
            (assoc_id, message_id, embed_id, position, now),
        )

        logger.debug(f"Embed {embed_id} attached to message {message_id}")

        return True

    def remove_embed_from_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, embed_id: SnowflakeID
    ) -> bool:
        """
        Remove an embed from a message.

        Args:
            user_id: ID of user removing embed
            message_id: ID of message
            embed_id: ID of embed to remove

        Returns:
            True if removed

        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only remove embeds from own messages")

        self._db.execute(
            "DELETE FROM embed_message_embeds WHERE message_id = ? AND embed_id = ?",
            (message_id, embed_id),
        )

        logger.debug(f"Embed {embed_id} removed from message {message_id}")

        return True

    def get_message_embeds(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[Embed]:
        """
        Get all embeds attached to a message.

        Args:
            user_id: ID of user requesting
            message_id: ID of message

        Returns:
            List of Embed objects

        Raises:
            MessageNotFoundError: Message not found
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        rows = self._db.fetch_all(
            """SELECT e.*, me.suppressed, me.position
               FROM embed_embeds e
               INNER JOIN embed_message_embeds me ON e.id = me.embed_id
               WHERE me.message_id = ? AND me.suppressed = 0
               ORDER BY me.position""",
            (message_id,),
        )

        return [self._row_to_embed(row) for row in rows]

    def suppress_embeds(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """
        Suppress (hide) all embeds on a message.

        Args:
            user_id: ID of user suppressing
            message_id: ID of message

        Returns:
            True if suppressed

        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only suppress embeds on own messages")

        self._db.execute(
            "UPDATE embed_message_embeds SET suppressed = 1 WHERE message_id = ?",
            (message_id,),
        )

        logger.debug(f"Embeds suppressed on message {message_id}")

        return True

    def unsuppress_embeds(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """
        Unsuppress (show) all embeds on a message.

        Args:
            user_id: ID of user unsuppressing
            message_id: ID of message

        Returns:
            True if unsuppressed

        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only unsuppress embeds on own messages")

        self._db.execute(
            "UPDATE embed_message_embeds SET suppressed = 0 WHERE message_id = ?",
            (message_id,),
        )

        logger.debug(f"Embeds unsuppressed on message {message_id}")

        return True
