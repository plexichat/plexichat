"""
Attachment service - Business logic for message attachments.
"""

from typing import Any, Dict, List, Optional

from ..models import Attachment
from ..repositories.attachment import AttachmentRepository
from ..repositories.message import MessageRepository
from ..exceptions import (
    MessageNotFoundError,
    MessageAccessDeniedError,
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    AttachmentLimitError,
)
from .base import BaseService
from .participant import ParticipantService
from .user_settings import UserSettingsService
from src.core.base import SnowflakeID
from src.utils.encryption import encrypt_data


class AttachmentService(BaseService):
    """Service for attachment operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
        user_settings_service: UserSettingsService,
    ) -> None:
        super().__init__(db)
        self._repo = AttachmentRepository(db)
        self._message_repo = MessageRepository(db)
        self._participant_svc = participant_service
        self._user_settings_svc = user_settings_service

    def add_attachment(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        filename: str,
        content_type: str,
        size: int,
        url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Attachment:
        """Add an attachment to a message."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        if msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only add attachments to own messages")

        # Check size limit
        user_settings = self._user_settings_svc.get_message_settings(user_id)
        max_size = user_settings.max_attachment_size or self._get_config(
            "max_attachment_size", 10485760
        )

        if size > max_size:
            raise AttachmentTooLargeError(
                f"Attachment exceeds maximum size of {max_size} bytes", max_size, size
            )

        # Check attachment count
        existing_count = self._repo.count_by_message(message_id)
        max_count = user_settings.max_attachments_per_message or self._get_config(
            "max_attachments_per_message", 10
        )

        if existing_count >= max_count:
            raise AttachmentLimitError(
                f"Message already has maximum attachments ({max_count})",
                max_count,
                existing_count,
            )

        now = self._get_timestamp()
        att_id = self._generate_id()

        # Encrypt URL if enabled
        encrypted_url = None
        final_url = url
        if self._get_config("encrypt_attachments"):
            encrypted_url = encrypt_data(url)
            final_url = "[encrypted]"

        self._repo.create(
            att_id,
            message_id,
            filename,
            content_type,
            size,
            final_url,
            now,
            url_encrypted=encrypted_url,
            metadata=metadata,
        )

        row = self._repo.get_by_id(att_id)
        if row is None:
            raise AttachmentNotFoundError("Failed to create attachment")
        return self._repo.row_to_model(row)

    def get_attachments(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[Attachment]:
        """Get attachments for a message."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            return []

        if not self._participant_svc.is_participant(msg_row["conversation_id"], user_id):
            return []

        rows = self._repo.get_by_message(message_id)
        return [self._repo.row_to_model(row) for row in rows]

    def delete_attachment(
        self, user_id: SnowflakeID, attachment_id: SnowflakeID
    ) -> bool:
        """Delete an attachment."""
        row = self._repo.get_by_id(attachment_id)
        if not row:
            raise AttachmentNotFoundError("Attachment not found")

        msg_row = self._message_repo.get_by_id(row["message_id"])
        if not msg_row or msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only delete own attachments")

        self._repo.soft_delete(attachment_id)
        return True

    def get_batch_by_messages(
        self, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, List[Attachment]]:
        """Get attachments for multiple messages (batch operation)."""
        return self._repo.get_batch_by_messages(message_ids)
