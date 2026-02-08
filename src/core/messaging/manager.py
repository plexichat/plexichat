"""
Messaging manager - Facade for messaging operations.

This is the refactored manager that coordinates specialized services
while maintaining backward compatibility with the existing API.
"""

from typing import Any, Dict, List, Optional

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID

from .models import (
    Message,
    Conversation,
    Participant,
    MessageStatus,
    Attachment,
    ContentFilter,
    UserMessageSettings,
    ConversationType,
    MessageStatusType,
    ParticipantRole,
)
from .exceptions import (
    ConversationNotFoundError,
    ConversationAccessDeniedError,
    ParticipantNotFoundError,
    ParticipantLimitError,
    ConversationTypeError,
    InvalidRecipientError,
)
from .schema import create_tables
from .services import (
    ConversationService,
    MessageService,
    ParticipantService,
    MessageStatusService,
    AttachmentService,
    PinService,
    UserSettingsService,
    ContentFilterService,
)
from .events import MessagingEventBus, get_event_bus

from . import exceptions as _exc
from . import models as _models


class MessagingManager(BaseManager):
    """
    Core messaging manager - Facade coordinating specialized services.

    This manager provides a unified API for all messaging operations while
    delegating to specialized services for actual business logic.
    """

    # Re-expose exceptions for easy access
    MessagingError = _exc.MessagingError
    ConversationNotFoundError = _exc.ConversationNotFoundError
    ConversationAccessDeniedError = _exc.ConversationAccessDeniedError
    ConversationTypeError = _exc.ConversationTypeError
    MessageNotFoundError = _exc.MessageNotFoundError
    MessageAccessDeniedError = _exc.MessageAccessDeniedError
    ParticipantNotFoundError = _exc.ParticipantNotFoundError
    ParticipantExistsError = _exc.ParticipantExistsError
    ParticipantLimitError = _exc.ParticipantLimitError
    InvalidContentError = _exc.InvalidContentError
    ContentTooLongError = _exc.ContentTooLongError
    AttachmentError = _exc.AttachmentError
    AttachmentTooLargeError = _exc.AttachmentTooLargeError
    AttachmentLimitError = _exc.AttachmentLimitError
    RateLimitError = _exc.RateLimitError
    InvalidRecipientError = _exc.InvalidRecipientError
    PinLimitError = _exc.PinLimitError
    MessageNotPinnedError = _exc.MessageNotPinnedError
    AttachmentNotFoundError = _exc.AttachmentNotFoundError

    # Re-expose models/enums for easy access
    ConversationType = _models.ConversationType
    MessageType = _models.MessageType
    MessageStatusType = _models.MessageStatusType
    ParticipantRole = _models.ParticipantRole
    FilterAction = _models.FilterAction

    def __init__(self, db: Any, auth_module: Optional[Any] = None) -> None:
        """
        Initialize the messaging manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for permission checks
        """
        super().__init__(db, auth_module)
        self._config = config.get("messaging", {})

        # Create tables
        create_tables(db)

        # Initialize services
        self._participant_svc = ParticipantService(db)
        self._user_settings_svc = UserSettingsService(db)
        self._content_filter_svc = ContentFilterService(db)

        self._conversation_svc = ConversationService(
            db,
            self._participant_svc,
            self._user_settings_svc,
            self._content_filter_svc,
        )

        self._message_svc = MessageService(
            db,
            self._participant_svc,
            self._user_settings_svc,
            self._content_filter_svc,
        )

        self._message_status_svc = MessageStatusService(
            db,
            self._participant_svc,
            self._user_settings_svc,
        )

        self._attachment_svc = AttachmentService(
            db,
            self._participant_svc,
            self._user_settings_svc,
        )

        self._pin_svc = PinService(db, self._participant_svc)

        # Event bus for reliable event delivery
        self._event_bus = get_event_bus()

        logger.info("Messaging module initialized (refactored)")

    # === Event Bus Access ===

    @property
    def event_bus(self) -> MessagingEventBus:
        """Get the event bus for subscribing to messaging events."""
        return self._event_bus

    # === Conversations ===

    def create_dm(
        self,
        user_id: SnowflakeID,
        recipient_id: SnowflakeID,
        auto_create: Optional[bool] = None,
    ) -> Conversation:
        """Create or get existing DM conversation."""
        return self._conversation_svc.create_dm(user_id, recipient_id, auto_create)

    def create_group(
        self,
        owner_id: SnowflakeID,
        name: str,
        participant_ids: Optional[List[SnowflakeID]] = None,
        max_participants: Optional[int] = None,
    ) -> Conversation:
        """Create a group conversation."""
        conv = self._conversation_svc.create_group(
            owner_id, name, participant_ids, max_participants
        )

        # Send system message
        self.send_system_message(
            conv.id,
            f'Group "{name}" created',
            "group_created",
            {"creator_id": owner_id},
        )

        return conv

    def get_or_create_notes(self, user_id: SnowflakeID) -> Conversation:
        """Get or create a personal notes conversation for a user."""
        return self._conversation_svc.get_or_create_notes(user_id)

    def create_server_channel_conversation(
        self, server_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Conversation:
        """Create a conversation for a server channel."""
        return self._conversation_svc.create_server_channel_conversation(
            server_id, channel_id
        )

    def create_thread_conversation(
        self, server_id: SnowflakeID, channel_id: SnowflakeID, name: str
    ) -> Conversation:
        """Create a conversation for a thread."""
        return self._conversation_svc.create_thread_conversation(
            server_id, channel_id, name
        )

    def get_conversation(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Conversation]:
        """Get a conversation by ID if user has access."""
        return self._conversation_svc.get_conversation(conversation_id, user_id)

    def get_conversations(
        self,
        user_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        conversation_type: Optional[ConversationType] = None,
    ) -> List[Conversation]:
        """Get user's conversations with pagination."""
        return self._conversation_svc.get_conversations(
            user_id, limit, before_id, conversation_type
        )

    def update_conversation(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        name: Optional[str] = None,
        max_participants: Optional[int] = None,
    ) -> Conversation:
        """Update conversation settings."""
        return self._conversation_svc.update_conversation(
            user_id, conversation_id, name, max_participants
        )

    def delete_conversation(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> bool:
        """Delete a conversation (soft delete)."""
        return self._conversation_svc.delete_conversation(user_id, conversation_id)

    def leave_conversation(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> bool:
        """Leave a conversation."""
        result = self._conversation_svc.leave_conversation(user_id, conversation_id)

        if result:
            # Send system message
            self.send_system_message(
                conversation_id,
                "A user left the conversation",
                "user_left",
                {"user_id": user_id},
            )

        return result

    # === Participants ===

    def add_participant(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        participant_id: SnowflakeID,
        role: ParticipantRole = ParticipantRole.MEMBER,
    ) -> Participant:
        """Add a participant to a group conversation."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")

        if conv.conversation_type == ConversationType.DM:
            raise ConversationTypeError("Cannot add participants to DM")

        # Check permission
        self._participant_svc.require_permission(
            conversation_id,
            user_id,
            [ParticipantRole.OWNER, ParticipantRole.ADMIN],
            "Only owner or admin can add participants",
        )

        # Validate participant exists
        if not self._user_settings_svc.user_exists(participant_id):
            raise InvalidRecipientError(f"User {participant_id} does not exist")

        # Check participant limit
        if conv.participant_count >= conv.max_participants:
            raise ParticipantLimitError(
                "Conversation has reached maximum participants",
                conv.max_participants,
                conv.participant_count,
            )

        # Cannot add as owner
        if role == ParticipantRole.OWNER:
            raise ConversationAccessDeniedError("Cannot add participant as owner")

        participant = self._participant_svc.add_participant(
            conversation_id, participant_id, role
        )

        # Send system message
        self.send_system_message(
            conversation_id,
            "A user was added to the conversation",
            "user_added",
            {"user_id": participant_id, "added_by": user_id},
        )

        return participant

    def add_participant_to_conversations(
        self,
        user_id: SnowflakeID,
        conversation_ids: List[SnowflakeID],
        role: ParticipantRole = ParticipantRole.MEMBER,
    ) -> None:
        """Add a participant to multiple conversations (for server joins)."""
        self._participant_svc.add_user_to_multiple_conversations(
            user_id, conversation_ids, role
        )

    def remove_participant_from_conversations(
        self,
        user_id: SnowflakeID,
        conversation_ids: List[SnowflakeID],
    ) -> None:
        """Remove a participant from multiple conversations (for server leaves/kicks)."""
        self._participant_svc.remove_user_from_multiple_conversations(
            user_id, conversation_ids
        )

    def remove_participant(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        participant_id: SnowflakeID,
    ) -> bool:
        """Remove a participant from a group conversation."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")

        if conv.conversation_type == ConversationType.DM:
            raise ConversationTypeError("Cannot remove participants from DM")

        actor = self._participant_svc.get_participant(conversation_id, user_id)
        if not actor:
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        target = self._participant_svc.get_participant(conversation_id, participant_id)
        if not target:
            raise ParticipantNotFoundError("Participant not found")

        # Permission checks
        if actor.role == ParticipantRole.OWNER:
            if participant_id == user_id:
                raise ConversationAccessDeniedError(
                    "Owner cannot remove themselves, use leave instead"
                )
        elif actor.role == ParticipantRole.ADMIN:
            if target.role in [ParticipantRole.OWNER, ParticipantRole.ADMIN]:
                raise ConversationAccessDeniedError(
                    "Admin cannot remove owner or other admins"
                )
        else:
            raise ConversationAccessDeniedError(
                "Only owner or admin can remove participants"
            )

        self._participant_svc.remove_participant(conversation_id, participant_id)

        # Send system message
        self.send_system_message(
            conversation_id,
            "A user was removed from the conversation",
            "user_removed",
            {"user_id": participant_id, "removed_by": user_id},
        )

        return True

    def update_participant_role(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        participant_id: SnowflakeID,
        role: ParticipantRole,
    ) -> Participant:
        """Update a participant's role."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")

        if conv.conversation_type == ConversationType.DM:
            raise ConversationTypeError("Cannot change roles in DM")

        actor = self._participant_svc.get_participant(conversation_id, user_id)
        if not actor:
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        target = self._participant_svc.get_participant(conversation_id, participant_id)
        if not target:
            raise ParticipantNotFoundError("Participant not found")

        # Only owner can change roles
        if actor.role != ParticipantRole.OWNER:
            raise ConversationAccessDeniedError("Only owner can change roles")

        if user_id == participant_id:
            raise ConversationAccessDeniedError("Cannot change own role")

        if role == ParticipantRole.OWNER:
            raise ConversationAccessDeniedError(
                "Cannot assign owner role, use transfer ownership"
            )

        return self._participant_svc.update_role(conversation_id, participant_id, role)

    def get_participants(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> List[Participant]:
        """Get all participants in a conversation."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        return self._participant_svc.get_all_participants(conversation_id)

    def get_participant_ids(self, conversation_id: SnowflakeID) -> List[SnowflakeID]:
        """Get all participant user IDs (internal use for event routing)."""
        return self._participant_svc.get_participant_ids(conversation_id)

    def mute_conversation(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        muted: bool = True,
        until: Optional[int] = None,
    ) -> bool:
        """Mute or unmute a conversation for a user."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        self._participant_svc.update_mute(conversation_id, user_id, muted, until)
        return True

    # === Messages ===

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
        return self._message_svc.send_message(
            user_id,
            conversation_id,
            content,
            message_type,
            reply_to_id,
            attachments,
            embeds,
            webhook_id,
        )

    def edit_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, content: str
    ) -> Message:
        """Edit a message (own messages only)."""
        return self._message_svc.edit_message(user_id, message_id, content)

    def delete_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, hard_delete: bool = False
    ) -> bool:
        """Delete a message."""
        return self._message_svc.delete_message(user_id, message_id, hard_delete)

    def get_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> Optional[Message]:
        """Get a single message by ID."""
        return self._message_svc.get_message(user_id, message_id)

    def get_messages(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Message]:
        """Get messages from a conversation with cursor pagination."""
        messages = self._message_svc.get_messages(
            user_id, conversation_id, limit, before_id, after_id
        )

        # Enrich with status info
        if messages:
            message_ids = [m.id for m in messages]
            status_map = self._message_status_svc.get_batch_status_info(
                user_id, message_ids
            )

            for msg in messages:
                stats = status_map.get(msg.id, {})
                msg.status = stats.get("status", MessageStatusType.SENT)
                msg.delivery_count = stats.get("delivery_count", 0)
                msg.read_count = stats.get("read_count", 0)

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
        messages = self._message_svc.search_messages(
            user_id, conversation_id, query, limit, before_id, after_id
        )

        # Enrich with status info
        if messages:
            message_ids = [m.id for m in messages]
            status_map = self._message_status_svc.get_batch_status_info(
                user_id, message_ids
            )

            for msg in messages:
                stats = status_map.get(msg.id, {})
                msg.status = stats.get("status", MessageStatusType.SENT)
                msg.delivery_count = stats.get("delivery_count", 0)
                msg.read_count = stats.get("read_count", 0)

        return messages

    def send_system_message(
        self,
        conversation_id: SnowflakeID,
        content: str,
        event_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Send a system message."""
        msg = self._message_svc.send_system_message(
            conversation_id, content, event_type, metadata
        )

        # Update conversation's last message
        self._conversation_svc.update_last_message(
            conversation_id, msg.id, msg.created_at
        )

        return msg

    # === Message Status ===

    def mark_delivered(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> int:
        """Mark messages as delivered."""
        return self._message_status_svc.mark_delivered(user_id, message_ids)

    def mark_read(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        up_to_message_id: Optional[SnowflakeID] = None,
    ) -> int:
        """Mark messages as read."""
        return self._message_status_svc.mark_read(
            user_id, conversation_id, up_to_message_id
        )

    def get_unread_count(
        self, user_id: SnowflakeID, conversation_id: Optional[SnowflakeID] = None
    ) -> Dict[SnowflakeID, int]:
        """Get unread message counts."""
        return self._message_status_svc.get_unread_count(user_id, conversation_id)

    def get_message_status(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[MessageStatus]:
        """Get delivery/read status for a message (sender only)."""
        return self._message_status_svc.get_message_status(user_id, message_id)

    def get_reader_ids(self, user_id: SnowflakeID, message_id: SnowflakeID) -> List[SnowflakeID]:
        """Get IDs of users who have read a message (sender only)."""
        return self._message_status_svc.get_reader_ids(user_id, message_id)

    def get_batch_reader_ids(self, user_id: SnowflakeID, message_ids: List[SnowflakeID]) -> Dict[SnowflakeID, List[SnowflakeID]]:
        """Get IDs of users who have read messages (batch, sender only)."""
        return self._message_status_svc.get_batch_reader_ids(user_id, message_ids)

    # === Pins ===

    def pin_message(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """Pin a message in its conversation."""
        return self._pin_svc.pin_message(user_id, message_id)

    def unpin_message(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """Unpin a message."""
        return self._pin_svc.unpin_message(user_id, message_id)

    def get_pinned_messages(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> List[Message]:
        """Get all pinned messages in a conversation."""
        return self._pin_svc.get_pinned_messages(user_id, conversation_id)

    # === Content Filtering ===

    def get_user_filter_settings(self, user_id: SnowflakeID) -> ContentFilter:
        """Get user's content filter settings."""
        return self._content_filter_svc.get_filter_settings(user_id)

    def update_user_filter_settings(
        self,
        user_id: SnowflakeID,
        profanity_filter: Optional[bool] = None,
        nsfw_filter: Optional[bool] = None,
        spoiler_click_to_reveal: Optional[bool] = None,
        custom_blocked_words: Optional[List[str]] = None,
    ) -> ContentFilter:
        """Update user's content filter settings."""
        return self._content_filter_svc.update_filter_settings(
            user_id,
            profanity_filter,
            nsfw_filter,
            spoiler_click_to_reveal,
            custom_blocked_words,
        )

    # === User Message Settings ===

    def get_user_message_settings(self, user_id: SnowflakeID) -> UserMessageSettings:
        """Get user's message settings."""
        return self._user_settings_svc.get_message_settings(user_id)

    def update_user_message_settings(
        self,
        user_id: SnowflakeID,
        allow_dms_from: Optional[str] = None,
        auto_create_dms: Optional[bool] = None,
        max_message_length: Optional[int] = None,
        max_attachment_size: Optional[int] = None,
        max_attachments_per_message: Optional[int] = None,
        read_receipts_enabled: Optional[bool] = None,
        typing_indicators_enabled: Optional[bool] = None,
    ) -> UserMessageSettings:
        """Update user's message settings."""
        return self._user_settings_svc.update_message_settings(
            user_id,
            allow_dms_from,
            auto_create_dms,
            max_message_length,
            max_attachment_size,
            max_attachments_per_message,
            read_receipts_enabled,
            typing_indicators_enabled,
        )

    # === Attachments ===

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
        return self._attachment_svc.add_attachment(
            user_id, message_id, filename, content_type, size, url, metadata
        )

    def get_attachments(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[Attachment]:
        """Get attachments for a message."""
        return self._attachment_svc.get_attachments(user_id, message_id)

    def delete_attachment(
        self, user_id: SnowflakeID, attachment_id: SnowflakeID
    ) -> bool:
        """Delete an attachment."""
        return self._attachment_svc.delete_attachment(user_id, attachment_id)

    # === Internal helpers for backward compatibility ===

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        """Check if user is a participant (for backward compatibility)."""
        return self._participant_svc.is_participant(conversation_id, user_id)

    def _get_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Participant]:
        """Get participant record (for backward compatibility)."""
        return self._participant_svc.get_participant(conversation_id, user_id)

    def _get_message_raw(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get raw message row (for backward compatibility)."""
        return self._message_svc.get_message_raw(message_id)
