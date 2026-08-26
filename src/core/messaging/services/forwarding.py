"""
Message forwarding service - Business logic for forwarding messages.

Handles forwarding messages between conversations/DMs with proper
attribution, content preservation, and permission checks.
"""

import time
from typing import List, Dict, Any, Optional

import utils.logger as logger
from src.utils.encryption import (
    decrypt_data,
    decrypt_message,
    generate_snowflake_id,
    is_message_encrypted,
)


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

    def _safe_send_via_messaging(
        self, user_id: int, target_conversation_id: int, content: str
    ) -> Optional[Any]:
        """Try to send the forwarded message via the messaging module.

        Returns the new Message on success, or None if the messaging module
        is unavailable, not initialized, or the send raised an exception.
        The forwarding record is still written even if the live send fails
        so the forward isn't lost (a follow-up background sync can pick
        up the slack).
        """
        if not self._messaging:
            return None
        send = getattr(self._messaging, "send_message", None)
        if send is None or not callable(send):
            return None
        try:
            return send(
                user_id=user_id,
                conversation_id=target_conversation_id,
                content=content,
            )
        except Exception as e:
            logger.warning(
                "Forward: messaging.send_message raised %s; recording forward without live message",
                e,
            )
            return None

    @staticmethod
    def _extract_message_id(message: Any) -> Optional[int]:
        """Best-effort extraction of the snowflake ID from a Message object."""
        if message is None:
            return None
        for attr in ("id", "message_id", "snowflake_id"):
            value = getattr(message, attr, None)
            if value is not None:
                return value
        if isinstance(message, dict):
            return message.get("id") or message.get("message_id")
        return None

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
            "SELECT id, conversation_id, author_id, content, content_encrypted, created_at "
            "FROM msg_messages WHERE id = ? AND deleted = 0",
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

        # Create the forwarded message via the messaging module, if available.
        # The send is best-effort: even if it fails (e.g. content filter
        # rejected a forwarded blob) we still want to record the forward so
        # the audit trail isn't lost.
        forward_id = self._generate_id()
        now = self._get_timestamp()

        # L3 fix: NEVER concatenate the stored content verbatim. Stored
        # content may be an ENC envelope (ciphertext), which previously
        # produced literal-garbage forwards AND duplicated the ciphertext
        # into msg_forwarded.original_content. Instead the original is
        # decrypted to plaintext here and re-sent through
        # messaging.send_message, which encrypts for the DESTINATION
        # conversation with the destination key. Undecryptable content is
        # rejected with a clear error rather than forwarded as garbage.
        original_content = self._decrypt_stored_content(original)
        if original_content is None:
            raise ValueError(
                "Cannot forward this message: its encrypted content could "
                "not be decrypted on this server"
            )

        forward_prefix = (
            f"🔄 Forwarded message (originally from <@{original['author_id']}>)\n"
        )
        forward_content = forward_prefix + original_content

        new_message = self._safe_send_via_messaging(
            user_id, target_conversation_id, forward_content
        )
        new_message_id = self._extract_message_id(new_message) or self._generate_id()

        # Record the forward WITHOUT duplicating the original content:
        # msg_forwarded.original_content stays NULL (the plaintext lives in
        # exactly one place — the source row, still subject to its own
        # encryption-at-rest contract).
        self._db.execute(
            """INSERT INTO msg_forwarded
               (id, message_id, original_message_id, original_conversation_id,
                original_author_id, forwarded_by, original_content, original_created_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                forward_id,
                new_message_id,
                message_id,
                original["conversation_id"],
                original["author_id"],
                user_id,
                None,
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
            "new_message_id": new_message_id,
            "target_conversation_id": target_conversation_id,
            "forwarded_by": user_id,
            "created_at": now,
        }

    @staticmethod
    def _decrypt_stored_content(row: Dict[str, Any]) -> Optional[str]:
        """Decrypt a stored msg_messages content value to plaintext.

        Mirrors MessageRepository.row_to_model: ENC envelopes are
        decrypted with the message id (and conversation id for ratchet
        envelopes); legacy rows fall back to the content_encrypted mirror.
        Returns None when decryption is impossible so callers can reject
        instead of forwarding ciphertext garbage.
        """
        content = row.get("content") or ""
        if is_message_encrypted(content):
            try:
                if content.startswith("ENC:3:"):
                    return decrypt_message(
                        content,
                        row["id"],
                        conversation_id=row.get("conversation_id"),
                    )
                return decrypt_message(content, row["id"])
            except Exception as e:
                logger.warning(
                    "Forward: failed to decrypt original message %s: %s",
                    row.get("id"),
                    e,
                )
                return None
        if row.get("content_encrypted") and content == "[encrypted]":
            try:
                return decrypt_data(row["content_encrypted"])
            except Exception as e:
                logger.warning(
                    "Forward: failed to decrypt legacy content of %s: %s",
                    row.get("id"),
                    e,
                )
                return None
        return content

    def get_forward_history(
        self, message_id: int, requester_id: int
    ) -> List[Dict[str, Any]]:
        """Get forwarding history for a message (participant-only).

        L3 fix: reads now REQUIRE the caller to be a participant of the
        original message's conversation; previously ANY caller could
        enumerate where a message was forwarded (cross-conversation
        disclosure). Fails closed when no participation checker exists.
        """
        original = self._db.fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ?",
            (message_id,),
        )
        if not original:
            raise ValueError("Original message not found")

        if not self._participant_svc or not self._participant_svc.is_participant(
            original["conversation_id"], requester_id
        ):
            raise PermissionError("Cannot view forwarding history for this message")

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
