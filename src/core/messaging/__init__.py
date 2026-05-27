"""
Messaging module - Secure messaging for Plexichat API.

This module provides:
- Direct messages (DMs) and group conversations
- Message CRUD with Snowflake IDs
- Rich text with formatting and content filtering
- Message attachments with configurable limits
- Delivery and read receipts
- Participant management with roles
- Server-specific permissions

Usage:
    # In main.py (setup once)
    from src.core.messaging import setup
    from src.core.database import Database
    from src.core import auth

    db = Database()
    db.connect()
    auth.setup(db)
    messaging.setup(db)

    # In any other file
    from src.core import messaging

    conv = messaging.create_dm(user_id, recipient_id)
    msg = messaging.send_message(user_id, conv.id, "Hello!")
"""

from typing import Optional, List, Dict, Any

from .exceptions import (
    MessagingError,
    ConversationNotFoundError,
    ConversationAccessDeniedError,
    ConversationTypeError,
    MessageNotFoundError,
    MessageAccessDeniedError,
    ParticipantNotFoundError,
    ParticipantExistsError,
    ParticipantLimitError,
    InvalidContentError,
    ContentTooLongError,
    AttachmentError,
    AttachmentTooLargeError,
    AttachmentLimitError,
    RateLimitError,
)
from .models import (
    Message,
    Conversation,
    Participant,
    MessageStatus,
    Attachment,
    ContentFilter,
    UserMessageSettings,
    ConversationType,
    MessageType,
    MessageStatusType,
    ParticipantRole,
    FilterAction,
)
from .events import (
    MessagingEventBus,
    MessagingEvent,
    MessagingEventType,
    EventResult,
    get_event_bus,
)

# Module state
_manager = None
_setup_complete = False


def setup(db, auth_module=None) -> None:
    """
    Initialize the messaging module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module reference for permission checks
    """
    global _manager, _setup_complete

    from .manager import MessagingManager

    _manager = MessagingManager(db, auth_module)
    _setup_complete = True


def _get_manager():
    """Get the messaging manager, ensuring setup was called."""
    if not _setup_complete:
        raise RuntimeError("Messaging not initialized. Call messaging.setup(db) first.")
    assert _manager is not None
    return _manager


def get_manager():
    """Get the messaging manager (public API)."""
    return _get_manager()


# === Conversations ===


def create_dm(
    user_id: int, recipient_id: int, auto_create: Optional[bool] = None
) -> Conversation:
    """
    Create or get existing DM conversation.

    Args:
        user_id: ID of user initiating
        recipient_id: ID of recipient
        auto_create: Override config for auto-creation behavior

    Returns:
        Conversation object

    Raises:
        ConversationAccessDeniedError: If user cannot message recipient
    """
    return _get_manager().create_dm(user_id, recipient_id, auto_create)


def create_group(
    owner_id: int,
    name: str,
    participant_ids: Optional[List[int]] = None,
    max_participants: Optional[int] = None,
) -> Conversation:
    """
    Create a group conversation.

    Args:
        owner_id: ID of group owner
        name: Group name
        participant_ids: Initial participants (owner auto-added)
        max_participants: Override default max participants

    Returns:
        Conversation object

    Raises:
        ParticipantLimitError: If too many initial participants
        InvalidContentError: If name is invalid
    """
    return _get_manager().create_group(
        owner_id, name, participant_ids, max_participants
    )


def create_server_channel_conversation(server_id: int, channel_id: int) -> Conversation:
    """
    Create a conversation for a server channel.

    Args:
        server_id: Server ID
        channel_id: Channel ID

    Returns:
        Conversation object
    """
    return _get_manager().create_server_channel_conversation(server_id, channel_id)


def create_thread_conversation(
    server_id: int, channel_id: int, name: str
) -> Conversation:
    """
    Create a conversation for a thread.

    Args:
        server_id: Server ID
        channel_id: Channel ID
        name: Thread name

    Returns:
        Conversation object
    """
    return _get_manager().create_thread_conversation(server_id, channel_id, name)


def get_or_create_notes(user_id: int) -> Conversation:
    """Get or create personal notes channel."""
    return _get_manager().get_or_create_notes(user_id)


def get_conversation(conversation_id: int, user_id: int) -> Optional[Conversation]:
    """Get a conversation by ID if user has access."""
    return _get_manager().get_conversation(conversation_id, user_id)


def get_conversations(
    user_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    conversation_type: Optional[ConversationType] = None,
) -> List[Conversation]:
    """
    Get user's conversations with pagination.

    Args:
        user_id: User ID
        limit: Max conversations to return
        before_id: Cursor for pagination (Snowflake ID)
        conversation_type: Filter by type (DM or GROUP)

    Returns:
        List of conversations ordered by last activity
    """
    return _get_manager().get_conversations(
        user_id, limit, before_id, conversation_type
    )


def update_conversation(
    user_id: int,
    conversation_id: int,
    name: Optional[str] = None,
    max_participants: Optional[int] = None,
) -> Conversation:
    """Update conversation settings (groups only, requires permission)."""
    return _get_manager().update_conversation(
        user_id, conversation_id, name, max_participants
    )


def delete_conversation(user_id: int, conversation_id: int) -> bool:
    """Delete a conversation (owner only for groups, either party for DMs)."""
    return _get_manager().delete_conversation(user_id, conversation_id)


def leave_conversation(user_id: int, conversation_id: int) -> bool:
    """Leave a conversation."""
    return _get_manager().leave_conversation(user_id, conversation_id)


# === Participants ===


def add_participant(
    user_id: int,
    conversation_id: int,
    participant_id: int,
    role: ParticipantRole = ParticipantRole.MEMBER,
) -> Participant:
    """Add a participant to a group conversation."""
    return _get_manager().add_participant(
        user_id, conversation_id, participant_id, role
    )


def add_participant_to_conversations(
    user_id: int,
    conversation_ids: list,
    role: ParticipantRole = ParticipantRole.MEMBER,
) -> None:
    """Add a participant to multiple conversations (for server joins)."""
    return _get_manager().add_participant_to_conversations(
        user_id, conversation_ids, role
    )


def remove_participant(user_id: int, conversation_id: int, participant_id: int) -> bool:
    """Remove a participant from a group conversation."""
    return _get_manager().remove_participant(user_id, conversation_id, participant_id)


def update_participant_role(
    user_id: int, conversation_id: int, participant_id: int, role: ParticipantRole
) -> Participant:
    """Update a participant's role."""
    return _get_manager().update_participant_role(
        user_id, conversation_id, participant_id, role
    )


def get_participants(user_id: int, conversation_id: int) -> List[Participant]:
    """Get all participants in a conversation."""
    return _get_manager().get_participants(user_id, conversation_id)


def get_participant_ids(conversation_id: int) -> List[int]:
    """Get all participant user IDs in a conversation."""
    return _get_manager().get_participant_ids(conversation_id)


def mute_conversation(
    user_id: int, conversation_id: int, muted: bool = True, until: Optional[int] = None
) -> bool:
    """Mute or unmute a conversation for a user."""
    return _get_manager().mute_conversation(user_id, conversation_id, muted, until)


def is_participant(conversation_id: int, user_id: int) -> bool:
    """
    Check if a user is a participant in a conversation.

    This includes both direct participants and server members for server channels.
    """
    return _get_manager().is_participant(conversation_id, user_id)


# === Messages ===


def send_message(
    user_id: int,
    conversation_id: int,
    content: str,
    message_type: MessageType = MessageType.TEXT,
    reply_to_id: Optional[int] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    webhook_id: Optional[int] = None,
) -> Message:
    """
    Send a message to a conversation.

    Args:
        user_id: Sender ID
        conversation_id: Target conversation
        content: Message content (validated and filtered)
        message_type: Type of message
        reply_to_id: Optional message ID to reply to
        attachments: Optional list of attachment data
        embeds: Optional list of rich embeds (stored in metadata)
        webhook_id: Optional webhook ID if sent via webhook

    Returns:
        Created Message object

    Raises:
        ConversationAccessDeniedError: If user cannot send to conversation
        InvalidContentError: If content fails validation
        ContentTooLongError: If content exceeds limit
        AttachmentError: If attachment is invalid
    """
    return _get_manager().send_message(
        user_id,
        conversation_id,
        content,
        message_type,
        reply_to_id,
        attachments,
        embeds,
        webhook_id,
    )


def edit_message(user_id: int, message_id: int, content: str) -> Message:
    """Edit a message (own messages only)."""
    return _get_manager().edit_message(user_id, message_id, content)


def update_message_metadata(
    message_id: int,
    metadata: Optional[Dict[str, Any]],
    merge: bool = True,
) -> Message:
    return _get_manager().update_message_metadata(message_id, metadata, merge)


def delete_message(user_id: int, message_id: int, hard_delete: bool = False) -> bool:
    """
    Delete a message.

    Args:
        user_id: User requesting deletion
        message_id: Message to delete
        hard_delete: If True, permanently delete (admin only)

    Returns:
        True if deleted
    """
    return _get_manager().delete_message(user_id, message_id, hard_delete)


def get_message(user_id: int, message_id: int) -> Optional[Message]:
    """Get a single message by ID."""
    return _get_manager().get_message(user_id, message_id)


def get_messages(
    user_id: int,
    conversation_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    after_id: Optional[int] = None,
) -> List[Message]:
    """
    Get messages from a conversation with cursor pagination.

    Args:
        user_id: User requesting messages
        conversation_id: Conversation to get messages from
        limit: Max messages to return
        before_id: Get messages before this ID (older)
        after_id: Get messages after this ID (newer)

    Returns:
        List of messages ordered by ID (newest first if before_id, oldest first if after_id)
    """
    return _get_manager().get_messages(
        user_id, conversation_id, limit, before_id, after_id
    )


def pin_message(user_id: int, message_id: int) -> bool:
    """Pin a message in its conversation."""
    return _get_manager().pin_message(user_id, message_id)


def unpin_message(user_id: int, message_id: int) -> bool:
    """Unpin a message."""
    return _get_manager().unpin_message(user_id, message_id)


def get_pinned_messages(user_id: int, conversation_id: int) -> List[Message]:
    """Get all pinned messages in a conversation."""
    return _get_manager().get_pinned_messages(user_id, conversation_id)


# === Message Status ===


def mark_delivered(user_id: int, message_ids: List[int]) -> int:
    """Mark messages as delivered. Returns count marked."""
    return _get_manager().mark_delivered(user_id, message_ids)


def mark_read(
    user_id: int, conversation_id: int, up_to_message_id: Optional[int] = None
) -> int:
    """
    Mark messages as read up to a specific message or all.

    Args:
        user_id: User marking as read
        conversation_id: Conversation
        up_to_message_id: Mark all messages up to and including this ID

    Returns:
        Count of messages marked as read
    """
    return _get_manager().mark_read(user_id, conversation_id, up_to_message_id)


def get_unread_count(
    user_id: int, conversation_id: Optional[int] = None
) -> Dict[int, int]:
    """
    Get unread message counts.

    Args:
        user_id: User ID
        conversation_id: Optional specific conversation

    Returns:
        Dict of conversation_id -> unread count
    """
    return _get_manager().get_unread_count(user_id, conversation_id)


def get_message_status(user_id: int, message_id: int) -> List[MessageStatus]:
    """Get delivery/read status for a message (sender only)."""
    return _get_manager().get_message_status(user_id, message_id)


def get_reader_ids(user_id: int, message_id: int) -> List[int]:
    """Get IDs of users who have read a message (sender only)."""
    return _get_manager().get_reader_ids(user_id, message_id)


def get_batch_reader_ids(user_id: int, message_ids: List[int]) -> Dict[int, List[int]]:
    """Get IDs of users who have read messages (batch, sender only)."""
    return _get_manager().get_batch_reader_ids(user_id, message_ids)


# === Content Filtering ===


def get_user_filter_settings(user_id: int) -> ContentFilter:
    """Get user's content filter settings."""
    return _get_manager().get_user_filter_settings(user_id)


def update_user_filter_settings(
    user_id: int,
    profanity_filter: Optional[bool] = None,
    nsfw_filter: Optional[bool] = None,
    spoiler_click_to_reveal: Optional[bool] = None,
    custom_blocked_words: Optional[List[str]] = None,
) -> ContentFilter:
    """Update user's content filter settings."""
    return _get_manager().update_user_filter_settings(
        user_id,
        profanity_filter,
        nsfw_filter,
        spoiler_click_to_reveal,
        custom_blocked_words,
    )


# === User Message Settings ===


def get_user_message_settings(user_id: int) -> UserMessageSettings:
    """Get user's message settings (limits, DM preferences, etc.)."""
    return _get_manager().get_user_message_settings(user_id)


def update_user_message_settings(
    user_id: int,
    allow_dms_from: Optional[str] = None,
    auto_create_dms: Optional[bool] = None,
    max_message_length: Optional[int] = None,
    max_attachment_size: Optional[int] = None,
    max_attachments_per_message: Optional[int] = None,
    read_receipts_enabled: Optional[bool] = None,
    typing_indicators_enabled: Optional[bool] = None,
) -> UserMessageSettings:
    """Update user's message settings."""
    return _get_manager().update_user_message_settings(
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
    user_id: int,
    message_id: int,
    filename: str,
    content_type: str,
    size: int,
    url: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Attachment:
    """Add an attachment to a message."""
    return _get_manager().add_attachment(
        user_id, message_id, filename, content_type, size, url, metadata
    )


def get_attachments(user_id: int, message_id: int) -> List[Attachment]:
    """Get attachments for a message."""
    return _get_manager().get_attachments(user_id, message_id)


def delete_attachment(user_id: int, attachment_id: int) -> bool:
    """Delete an attachment."""
    return _get_manager().delete_attachment(user_id, attachment_id)


# === System Messages ===


def send_system_message(
    conversation_id: int,
    content: str,
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Message:
    """Send a system message (internal use)."""
    return _get_manager().send_system_message(
        conversation_id, content, event_type, metadata
    )


__all__ = [
    # Setup
    "setup",
    "get_manager",
    # Exceptions
    "MessagingError",
    "ConversationNotFoundError",
    "ConversationAccessDeniedError",
    "ConversationTypeError",
    "MessageNotFoundError",
    "MessageAccessDeniedError",
    "ParticipantNotFoundError",
    "ParticipantExistsError",
    "ParticipantLimitError",
    "InvalidContentError",
    "ContentTooLongError",
    "AttachmentError",
    "AttachmentTooLargeError",
    "AttachmentLimitError",
    "RateLimitError",
    # Models
    "Message",
    "Conversation",
    "Participant",
    "MessageStatus",
    "Attachment",
    "ContentFilter",
    "UserMessageSettings",
    "ConversationType",
    "MessageType",
    "MessageStatusType",
    "ParticipantRole",
    "FilterAction",
    # Events
    "MessagingEventBus",
    "MessagingEvent",
    "MessagingEventType",
    "EventResult",
    "get_event_bus",
    # Conversations
    "create_dm",
    "create_group",
    "get_or_create_notes",
    "get_conversation",
    "get_conversations",
    "update_conversation",
    "delete_conversation",
    "leave_conversation",
    # Participants
    "add_participant",
    "add_participant_to_conversations",
    "remove_participant",
    "update_participant_role",
    "get_participants",
    "get_participant_ids",
    "mute_conversation",
    "is_participant",
    # Messages
    "send_message",
    "edit_message",
    "delete_message",
    "get_message",
    "get_messages",
    "pin_message",
    "unpin_message",
    "get_pinned_messages",
    # Message Status
    "mark_delivered",
    "mark_read",
    "get_unread_count",
    "get_message_status",
    "get_reader_ids",
    # Content Filtering
    "get_user_filter_settings",
    "update_user_filter_settings",
    # User Settings
    "get_user_message_settings",
    "update_user_message_settings",
    # Attachments
    "add_attachment",
    "get_attachments",
    "delete_attachment",
    # System
    "send_system_message",
]
