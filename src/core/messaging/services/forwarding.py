"""
Message forwarding service - Business logic for forwarding messages.

Handles forwarding messages between conversations/DMs with proper
attribution, content preservation, and permission checks.
"""

import time
from typing import List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


class ForwardingService:
    """Service for forwarding messages between conversations."""

    MAX_FORWARDS_PER_MESSAGE = 10
    MAX_FORWARDS_PER_USER_PER_HOUR = 50

    def __init__(self, db, messaging_module=None, participant_svc=None):
        self._db = db
        self._messaging = messaging_module
        self._participant_svc = participant_svc

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def forward_message(
        self,
        user_id: int,
        message_id: int,
        target_conversation_id: int,
    ) -> Dict[str, Any]:
        """
        Forward a message to another conversation.

        Args:
            user_id: ID of the user forwarding the message
            message_id: ID of the original message to forward
            target_conversation_id: ID of the conversation to forward to

        Returns:
            Forward record and new message info
        """
        # Get original message
        original = self._db.fetch_one(
            "SELECT id, conversation_id, author_id, content, created_at FROM msg_messages WHERE id = ? AND deleted = 0",
            (message_id,),
        )
        if not original:
            raise ValueError("Original message not found")

        # Check user can read the original message
        if self._participant_svc and not self._participant_svc.is_participant(
            original["conversation_id"], user_id
        ):
            raise PermissionError("Cannot read the original message")

        # Check user can write to target conversation
        if self._participant_svc and not self._participant_svc.is_participant(
            target_conversation_id, user_id
        ):
            raise PermissionError("Cannot send to the target conversation")

        # Rate limit check
        hour_ago = self._get_timestamp() - (3600 * 1000)
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM msg_forwarded WHERE forwarded_by = ? AND created_at > ?",
            (user_id, hour_ago),
        )
        if count_row and count_row["count"] >= self.MAX_FORWARDS_PER_USER_PER_HOUR:
            raise ValueError("Forward rate limit exceeded")

        # Check max forwards for original message
        fwd_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM msg_forwarded WHERE original_message_id = ?",
            (message_id,),
        )
        if fwd_count and fwd_count["count"] >= self.MAX_FORWARDS_PER_MESSAGE:
            raise ValueError("Message has been forwarded too many times")

        # Create the forwarded message via messaging module
        forward_id = self._generate_id()
        now = self._get_timestamp()
        new_message = None

        if self._messaging:
            try:
                # Format forwarded content with attribution
                forward_prefix = f"🔄 Forwarded message (originally from <@{original['author_id']}>)\n"
                forward_content = forward_prefix + (original["content"] or "")

                new_message = self._messaging.send_message(
                    user_id=user_id,
                    conversation_id=target_conversation_id,
                    content=forward_content,
                )
            except Exception as e:
                logger.error(
                    f"Failed to send forwarded message via messaging module: {e}"
                )
                raise

        # Record the forward
        self._db.execute(
            """INSERT INTO msg_forwarded
               (id, message_id, original_message_id, original_conversation_id,
                original_author_id, forwarded_by, original_content, original_created_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                forward_id,
                new_message.id if new_message else self._generate_id(),
                message_id,
                original["conversation_id"],
                original["author_id"],
                user_id,
                original["content"],
                original["created_at"],
                now,
            ),
        )

        logger.debug(
            f"User {user_id} forwarded message {message_id} to conversation {target_conversation_id}"
        )

        return {
            "forward_id": forward_id,
            "original_message_id": message_id,
            "original_author_id": original["author_id"],
            "original_conversation_id": original["conversation_id"],
            "new_message_id": new_message.id if new_message else None,
            "target_conversation_id": target_conversation_id,
            "forwarded_by": user_id,
            "created_at": now,
        }

    def get_forward_history(self, message_id: int) -> List[Dict[str, Any]]:
        """Get forwarding history for a message."""
        rows = self._db.fetch_all(
            "SELECT * FROM msg_forwarded WHERE original_message_id = ? ORDER BY created_at DESC",
            (message_id,),
        )
        return [dict(row) for row in rows]

    def get_forwards_by_user(
        self, user_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get forwarding history for a user."""
        rows = self._db.fetch_all(
            "SELECT * FROM msg_forwarded WHERE forwarded_by = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in rows]
