"""
Edit history service - Business logic for message edit history tracking.
"""

from typing import List, Dict, Any
import time

from .base import BaseService
from ..repositories.edit_history import EditHistoryRepository
from ..repositories.message import MessageRepository
from ..exceptions import MessageNotFoundError, ConversationAccessDeniedError
from .participant import ParticipantService
from src.core.base import SnowflakeID
from src.utils.encryption import encrypt_data, decrypt_data
import utils.logger as logger


class EditHistoryService(BaseService):
    """Service for message edit history operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
        encryption_enabled: bool = True,
    ) -> None:
        """Initialize edit history service."""
        super().__init__(db)
        self._repo = EditHistoryRepository(db)
        self._message_repo = MessageRepository(db)
        self._participant_svc = participant_service
        self._encryption_enabled = encryption_enabled

    def record_edit(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        old_content: str,
    ) -> bool:
        """
        Record a message edit in history.

        Args:
            user_id: ID of the user editing the message
            message_id: ID of the message being edited
            old_content: Previous content before edit

        Returns:
            True if recorded successfully

        Raises:
            MessageNotFoundError: If message doesn't exist
            ConversationAccessDeniedError: If user not in conversation
        """
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        if msg_row["deleted"]:
            raise MessageNotFoundError("Message not found")

        # Verify user can edit this message (author or moderator)
        if msg_row["author_id"] != user_id:
            # Could add moderator check here
            raise ConversationAccessDeniedError("Cannot edit this message")

        conversation_id = msg_row["conversation_id"]

        # Verify participant
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        # Get current version number
        current_version = self._repo.get_latest_version(message_id) or 0
        new_version = current_version + 1

        # Encrypt content if enabled
        old_content_encrypted = None
        if self._encryption_enabled:
            try:
                old_content_encrypted = encrypt_data(old_content)
            except Exception as e:
                logger.warning(f"Failed to encrypt edit history: {e}")

        now = int(time.time() * 1000)
        edit_id = self._generate_id()

        self._repo.create(
            edit_id=edit_id,
            message_id=message_id,
            editor_id=user_id,
            old_content=old_content,
            old_content_encrypted=old_content_encrypted,
            edit_timestamp=now,
            version_number=new_version,
        )

        logger.debug(
            f"Recorded edit {new_version} for message {message_id} by user {user_id}"
        )
        return True

    def get_edit_history(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
    ) -> List[Dict[str, Any]]:
        """
        Get edit history for a message.

        Args:
            user_id: ID of the user requesting history
            message_id: ID of the message

        Returns:
            List of edit history entries

        Raises:
            MessageNotFoundError: If message doesn't exist
            ConversationAccessDeniedError: If user not in conversation
        """
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        conversation_id = msg_row["conversation_id"]

        # Verify participant
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        rows = self._repo.get_by_message(message_id)

        # Decrypt if needed
        history = []
        for row in rows:
            entry = {
                "id": row["id"],
                "message_id": row["message_id"],
                "editor_id": row["editor_id"],
                "old_content": row["old_content"],
                "edit_timestamp": row["edit_timestamp"],
                "version_number": row["version_number"],
            }

            # Try to decrypt if encrypted version exists
            if row["old_content_encrypted"] and self._encryption_enabled:
                try:
                    entry["old_content"] = decrypt_data(row["old_content_encrypted"])
                except Exception as e:
                    logger.warning(f"Failed to decrypt edit history: {e}")
                    # Fall back to plain text if decryption fails

            history.append(entry)

        return history

    def get_edit_count(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
    ) -> int:
        """
        Get the number of edits for a message.

        Args:
            user_id: ID of the user requesting count
            message_id: ID of the message

        Returns:
            Number of edits

        Raises:
            MessageNotFoundError: If message doesn't exist
            ConversationAccessDeniedError: If user not in conversation
        """
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        conversation_id = msg_row["conversation_id"]

        # Verify participant
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        return self._repo.count_edits(message_id)
