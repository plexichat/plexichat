"""
Message service - Business logic for messages.
"""

from typing import Any, Dict, List, Optional

from ..models import Message, MessageType, Attachment
from ..repositories.message import MessageRepository
from ..repositories.attachment import AttachmentRepository
from ..repositories.pin import PinRepository
from ..exceptions import (
    ConversationAccessDeniedError,
    MessageNotFoundError,
    MessageAccessDeniedError,
    InvalidContentError,
    ContentTooLongError,
    AttachmentLimitError,
)
from .base import BaseService
from .participant import ParticipantService
from .user_settings import UserSettingsService
from .content_filter import ContentFilterService
from src.core.base import SnowflakeID
from src.utils.encryption import encrypt_message
import utils.logger as logger


class MessageService(BaseService):
    """Service for message operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
        user_settings_service: UserSettingsService,
        content_filter_service: ContentFilterService,
    ) -> None:
        super().__init__(db)
        self._repo = MessageRepository(db)
        self._attachment_repo = AttachmentRepository(db)
        self._pin_repo = PinRepository(db)
        self._participant_svc = participant_service
        self._user_settings_svc = user_settings_service
        self._content_filter_svc = content_filter_service

    def send_message(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        content: str,
        message_type: MessageType = MessageType.TEXT,
        reply_to_id: Optional[SnowflakeID] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        webhook_id: Optional[SnowflakeID] = None,
    ) -> Message:
        """Send a message to a conversation."""
        # Check access
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        # Get user settings for limits
        user_settings = self._user_settings_svc.get_message_settings(user_id)
        max_length = user_settings.max_message_length or self._get_config("max_message_length", 4000)

        # Validate content
        user_filter = self._content_filter_svc.get_filter_settings(user_id)
        content_result = self._content_filter_svc.validate_content(content, user_filter, max_length)

        if not content_result.valid:
            if any("exceeds maximum length" in issue for issue in content_result.issues):
                raise ContentTooLongError(
                    f"Message exceeds maximum length of {max_length}",
                    max_length,
                    len(content),
                )
            raise InvalidContentError("Invalid message content", content_result.issues)

        # Validate reply_to if provided
        if reply_to_id:
            if not self._repo.exists_in_conversation(reply_to_id, conversation_id):
                raise MessageNotFoundError("Reply target message not found in this conversation")

        # Validate attachments
        if attachments:
            max_attachments = (
                user_settings.max_attachments_per_message
                or self._get_config("max_attachments_per_message", 10)
            )
            if len(attachments) > max_attachments:
                raise AttachmentLimitError(
                    f"Cannot attach more than {max_attachments} files",
                    max_attachments,
                    len(attachments),
                )

        now = self._get_timestamp()
        msg_id = self._generate_id()

        # Encrypt content if enabled
        final_content = content_result.sanitized_content
        if self._get_config("encrypt_messages", True):
            final_content = encrypt_message(final_content, msg_id)

        # Build metadata
        metadata: Dict[str, Any] = {}
        if content_result.has_spoilers:
            metadata["has_spoilers"] = True
        if content_result.has_nsfw:
            metadata["has_nsfw"] = True
        if content_result.filtered_words:
            metadata["filtered"] = True
        if embeds:
            metadata["embeds"] = embeds

        # Use transaction for all DB writes
        try:
            self._repo.begin_transaction()

            self._repo.create(
                msg_id,
                conversation_id,
                user_id,
                final_content,
                message_type,
                now,
                reply_to_id=reply_to_id,
                metadata=metadata if metadata else None,
                webhook_id=webhook_id,
                auto_commit=False,
            )

            # Add attachments
            attachment_list: List[Attachment] = []
            if attachments:
                for att_data in attachments:
                    att_id = self._generate_id()
                    self._attachment_repo.create(
                        att_id,
                        msg_id,
                        att_data.get("filename", "file"),
                        att_data.get("content_type", "application/octet-stream"),
                        att_data.get("size", 0),
                        att_data.get("url", ""),
                        now,
                        metadata=att_data.get("metadata"),
                        auto_commit=False,
                    )
                    attachment_list.append(
                        Attachment(
                            id=att_id,
                            message_id=msg_id,
                            filename=att_data.get("filename", "file"),
                            content_type=att_data.get("content_type", "application/octet-stream"),
                            size=att_data.get("size", 0),
                            url=att_data.get("url", ""),
                            created_at=now,
                        )
                    )

            self._repo.commit()
        except Exception:
            self._repo.rollback()
            raise

        logger.debug(f"Message {msg_id} sent to conversation {conversation_id}")

        # Return message with original content (not encrypted)
        return Message(
            id=msg_id,
            conversation_id=conversation_id,
            author_id=user_id,
            content=content_result.sanitized_content,
            message_type=message_type,
            created_at=now,
            updated_at=now,
            reply_to_id=reply_to_id,
            edited=False,
            deleted=False,
            pinned=False,
            metadata=metadata if metadata else None,
            attachments=attachment_list,
        )

    def edit_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, content: str
    ) -> Message:
        """Edit a message (own messages only)."""
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        if msg_row["deleted"]:
            raise MessageNotFoundError("Message not found")

        if not self._participant_svc.is_participant(msg_row["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        if msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only edit own messages")

        # Validate content
        user_settings = self._user_settings_svc.get_message_settings(user_id)
        max_length = user_settings.max_message_length or self._get_config("max_message_length", 4000)
        user_filter = self._content_filter_svc.get_filter_settings(user_id)

        content_result = self._content_filter_svc.validate_content(content, user_filter, max_length)
        if not content_result.valid:
            raise InvalidContentError("Invalid message content", content_result.issues)

        now = self._get_timestamp()
        final_content = content_result.sanitized_content

        # Encrypt content if enabled
        if self._get_config("encrypt_messages", True):
            final_content = encrypt_message(final_content, message_id)

        self._repo.update_content(message_id, final_content, now)

        msg = self.get_message(user_id, message_id)
        if msg is None:
            raise MessageNotFoundError("Failed to retrieve updated message")
        return msg

    def delete_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, hard_delete: bool = False
    ) -> bool:
        """Delete a message."""
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        # Check permission - own message or admin in conversation
        can_delete = msg_row["author_id"] == user_id

        if not can_delete:
            from ..models import ParticipantRole
            participant = self._participant_svc.get_participant(msg_row["conversation_id"], user_id)
            if participant and participant.role in [ParticipantRole.OWNER, ParticipantRole.ADMIN]:
                can_delete = True

        if not can_delete:
            raise MessageAccessDeniedError("Cannot delete this message")

        now = self._get_timestamp()

        if hard_delete:
            self._repo.hard_delete(message_id)
        else:
            self._repo.soft_delete(message_id, now)

        return True

    def get_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> Optional[Message]:
        """Get a single message by ID."""
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row:
            return None

        if msg_row["deleted"]:
            return None

        if not self._participant_svc.is_participant(msg_row["conversation_id"], user_id):
            return None

        # Get pin info
        pin_info = self._pin_repo.get_by_message(message_id)

        msg = self._repo.row_to_model(msg_row, pin_info)

        # Get attachments
        att_rows = self._attachment_repo.get_by_message(message_id)
        msg.attachments = [self._attachment_repo.row_to_model(row) for row in att_rows]

        return msg

    def get_messages(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Message]:
        """Get messages from a conversation with cursor pagination."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        limit = min(limit, 100)

        rows = self._repo.get_by_conversation(conversation_id, limit, before_id, after_id)

        if not rows:
            return []

        message_ids = [row["id"] for row in rows]

        # Batch fetch pins and attachments
        pins_map = self._pin_repo.get_batch_by_messages(message_ids)
        attachments_map = self._attachment_repo.get_batch_by_messages(message_ids)

        messages = []
        for row in rows:
            pin_info = pins_map.get(row["id"])
            msg = self._repo.row_to_model(row, pin_info)
            msg.attachments = attachments_map.get(row["id"], [])
            messages.append(msg)

        return messages

    def search_messages(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        query: str,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Message]:
        """Search for messages in a conversation."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        limit = min(limit, 100)

        rows = self._repo.search(conversation_id, query, limit, before_id, after_id)

        if not rows:
            return []

        message_ids = [row["id"] for row in rows]

        # Batch fetch pins and attachments
        pins_map = self._pin_repo.get_batch_by_messages(message_ids)
        attachments_map = self._attachment_repo.get_batch_by_messages(message_ids)

        messages = []
        for row in rows:
            pin_info = pins_map.get(row["id"])
            msg = self._repo.row_to_model(row, pin_info)
            msg.attachments = attachments_map.get(row["id"], [])
            messages.append(msg)

        return messages

    def send_system_message(
        self,
        conversation_id: SnowflakeID,
        content: str,
        event_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Send a system message."""
        now = self._get_timestamp()
        msg_id = self._generate_id()

        full_metadata: Dict[str, Any] = {"event_type": event_type}
        if metadata:
            full_metadata.update(metadata)

        self._repo.create(
            msg_id,
            conversation_id,
            0,  # System author ID
            content,
            MessageType.SYSTEM,
            now,
            metadata=full_metadata,
        )

        row = self._repo.get_by_id(msg_id)
        if row is None:
            raise MessageNotFoundError("Failed to create system message")
        return self._repo.row_to_model(row)

    def get_message_raw(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get raw message row (for internal use)."""
        return self._repo.get_by_id(message_id)
