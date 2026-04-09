"""
Conversation service - Business logic for conversations.
"""

from typing import Any, List, Optional

from ..models import Conversation, ConversationType, ParticipantRole
from ..repositories.conversation import ConversationRepository
from ..exceptions import (
    ConversationNotFoundError,
    ConversationAccessDeniedError,
    ConversationTypeError,
    InvalidRecipientError,
    ParticipantLimitError,
    InvalidContentError,
)
from .base import BaseService
from .participant import ParticipantService
from .user_settings import UserSettingsService
from .content_filter import ContentFilterService
from src.core.base import SnowflakeID
from src.core.database import (
    invalidate_pattern,
    cache_get,
    cache_set,
    redis_available,
)
import utils.logger as logger


class ConversationService(BaseService):
    """Service for conversation operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
        user_settings_service: UserSettingsService,
        content_filter_service: ContentFilterService,
    ) -> None:
        super().__init__(db)
        self._repo = ConversationRepository(db)
        self._participant_svc = participant_service
        self._user_settings_svc = user_settings_service
        self._content_filter_svc = content_filter_service

    def create_dm(
        self,
        user_id: SnowflakeID,
        recipient_id: SnowflakeID,
        auto_create: Optional[bool] = None,
    ) -> Conversation:
        """Create or get existing DM conversation."""
        if user_id == recipient_id:
            raise InvalidRecipientError("Cannot create DM with yourself")

        # Validate recipient exists
        if not self._user_settings_svc.user_exists(recipient_id):
            raise InvalidRecipientError("Recipient does not exist")

        # Check recipient's DM settings
        recipient_settings = self._user_settings_svc.get_message_settings(recipient_id)
        if recipient_settings.allow_dms_from == "none":
            raise ConversationAccessDeniedError("Recipient does not accept DMs")

        # Check if DM already exists (with caching)
        u1, u2 = min(user_id, recipient_id), max(user_id, recipient_id)
        cache_key = f"dm_lookup:{u1}:{u2}"
        existing_id = cache_get(cache_key) if redis_available() else None

        if not existing_id:
            existing_id = self._repo.get_dm_lookup(user_id, recipient_id)
            if existing_id and redis_available():
                cache_set(cache_key, existing_id, ttl=3600)

        if existing_id:
            conv = self.get_conversation(existing_id, user_id)
            if conv:
                return conv

        # Check auto-create setting
        should_create = (
            auto_create
            if auto_create is not None
            else self._get_config("dm_auto_create", True)
        )

        if not should_create:
            raise ConversationNotFoundError(
                "DM does not exist and auto-create is disabled"
            )

        # Create new DM
        now = self._get_timestamp()
        conv_id = self._generate_id()
        encrypted = self._get_config("encrypt_messages", False)

        self._repo.create(
            conv_id,
            ConversationType.DM,
            now,
            max_participants=2,
            encrypted=encrypted,
        )

        # Add participants
        for uid in [user_id, recipient_id]:
            self._participant_svc.add_participant(conv_id, uid, ParticipantRole.MEMBER)

        # Create DM lookup
        lookup_id = self._generate_id()
        self._repo.create_dm_lookup(lookup_id, user_id, recipient_id, conv_id)

        logger.debug(
            f"Created DM conversation {conv_id} between {user_id} and {recipient_id}"
        )

        conv = self.get_conversation(conv_id, user_id)
        if conv is None:
            raise ConversationNotFoundError(
                f"Failed to retrieve created conversation {conv_id}"
            )
        return conv

    def create_group(
        self,
        owner_id: SnowflakeID,
        name: str,
        participant_ids: Optional[List[SnowflakeID]] = None,
        max_participants: Optional[int] = None,
    ) -> Conversation:
        """Create a group conversation."""
        # Validate name
        if not name or not name.strip():
            raise InvalidContentError("Group name cannot be empty")

        name = name.strip()
        if len(name) > 100:
            raise InvalidContentError("Group name cannot exceed 100 characters")

        # Validate content
        content_result = self._content_filter_svc.validate_content(name)
        if not content_result.valid:
            raise InvalidContentError("Invalid group name", content_result.issues)

        max_parts = (
            max_participants
            if max_participants is not None
            else self._get_config("max_group_participants", 100)
        )

        # Build participant list
        participants = list(set([owner_id] + (participant_ids or [])))
        if max_parts < 1:
            raise ParticipantLimitError(
                "Group must allow at least 1 participant", max_parts, len(participants)
            )
        if len(participants) > max_parts:
            raise ParticipantLimitError(
                f"Cannot create group with more than {max_parts} participants",
                max_parts,
                len(participants),
            )

        # Batch validate all participants exist
        existence = self._user_settings_svc.users_exist_batch(participants)
        for uid, exists in existence.items():
            if not exists:
                raise InvalidRecipientError(f"User {uid} does not exist")

        now = self._get_timestamp()
        conv_id = self._generate_id()
        encrypted = self._get_config("encrypt_messages", False)

        self._repo.create(
            conv_id,
            ConversationType.GROUP,
            now,
            name=content_result.sanitized_content,
            owner_id=owner_id,
            max_participants=max_parts,
            encrypted=encrypted,
        )

        # Add participants
        for uid in participants:
            role = ParticipantRole.OWNER if uid == owner_id else ParticipantRole.MEMBER
            self._participant_svc.add_participant(conv_id, uid, role)

        logger.debug(
            f"Created group conversation {conv_id} with {len(participants)} participants"
        )

        conv = self.get_conversation(conv_id, owner_id)
        if conv is None:
            raise ConversationNotFoundError(
                f"Failed to retrieve created conversation {conv_id}"
            )
        return conv

    def get_or_create_notes(self, user_id: SnowflakeID) -> Conversation:
        """Get or create a personal notes conversation for a user."""
        # Check if notes conversation already exists
        existing = self._repo.get_notes_conversation(user_id)
        if existing:
            conv = self.get_conversation(existing["id"], user_id)
            if conv:
                return conv

        # Create new notes conversation
        now = self._get_timestamp()
        conv_id = self._generate_id()
        encrypted = self._get_config("encrypt_messages", False)

        self._repo.create(
            conv_id,
            ConversationType.NOTES,
            now,
            name="Personal Notes",
            max_participants=1,
            encrypted=encrypted,
        )

        # Add user as sole participant
        self._participant_svc.add_participant(conv_id, user_id, ParticipantRole.OWNER)

        logger.debug(f"Created notes conversation {conv_id} for user {user_id}")

        conv = self.get_conversation(conv_id, user_id)
        if conv is None:
            raise ConversationNotFoundError(
                f"Failed to retrieve created notes conversation {conv_id}"
            )
        return conv

    def create_server_channel_conversation(
        self, server_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Conversation:
        """Create a conversation for a server channel."""
        now = self._get_timestamp()
        conv_id = self._generate_id()
        encrypted = self._get_config("encrypt_messages", False)
        metadata = {"server_id": server_id, "channel_id": channel_id}

        self._repo.create(
            conv_id,
            ConversationType.GROUP,
            now,
            encrypted=encrypted,
            metadata=metadata,
        )

        logger.debug(
            f"Created server channel conversation {conv_id} for channel {channel_id}"
        )

        return Conversation(
            id=conv_id,
            conversation_type=ConversationType.GROUP,
            created_at=now,
            updated_at=now,
            encrypted=encrypted,
            metadata=metadata,
        )

    def create_thread_conversation(
        self, server_id: SnowflakeID, channel_id: SnowflakeID, name: str
    ) -> Conversation:
        """Create a conversation for a thread."""
        now = self._get_timestamp()
        conv_id = self._generate_id()
        encrypted = self._get_config("encrypt_messages", False)
        metadata = {"server_id": server_id, "channel_id": channel_id}

        self._repo.create(
            conv_id,
            ConversationType.THREAD,
            now,
            name=name,
            encrypted=encrypted,
            metadata=metadata,
        )

        logger.debug(f"Created thread conversation {conv_id} for thread '{name}'")

        return Conversation(
            id=conv_id,
            conversation_type=ConversationType.THREAD,
            name=name,
            created_at=now,
            updated_at=now,
            encrypted=encrypted,
            metadata=metadata,
        )

    def get_conversation(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Conversation]:
        """Get a conversation by ID if user has access."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            return None

        row = self._repo.get_by_id(conversation_id)
        if not row:
            return None

        return self._repo.row_to_model(row)

    def get_conversations(
        self,
        user_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        conversation_type: Optional[ConversationType] = None,
    ) -> List[Conversation]:
        """Get user's conversations with pagination."""
        limit = min(limit, 100)

        rows = self._repo.get_user_conversations(
            user_id, limit, before_id, conversation_type
        )
        return [self._repo.row_to_model(row) for row in rows]

    def update_conversation(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        name: Optional[str] = None,
        max_participants: Optional[int] = None,
    ) -> Conversation:
        """Update conversation settings."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")

        conv_type = getattr(conv, "conversation_type", None)
        if isinstance(conv_type, str):
            conv_type = conv_type.lower()
        else:
            conv_type = getattr(conv_type, "value", conv_type)

        if conv_type == ConversationType.DM.value:
            raise ConversationTypeError("Cannot update DM settings")

        # Check permission
        self._participant_svc.require_permission(
            conversation_id,
            user_id,
            [ParticipantRole.OWNER, ParticipantRole.ADMIN],
            "Only owner or admin can update conversation",
        )

        validated_name = None
        if name is not None:
            name = name.strip()
            if not name:
                raise InvalidContentError("Group name cannot be empty")
            content_result = self._content_filter_svc.validate_content(name)
            if not content_result.valid:
                raise InvalidContentError("Invalid group name", content_result.issues)
            validated_name = content_result.sanitized_content

        if max_participants is not None and max_participants < conv.participant_count:
            raise ParticipantLimitError(
                "Cannot set max below current participant count",
                max_participants,
                conv.participant_count,
            )

        now = self._get_timestamp()
        self._repo.update(
            conversation_id,
            now,
            name=validated_name,
            max_participants=max_participants,
        )

        # Invalidate cache
        invalidate_pattern(f"conv_data:*int:{conversation_id}:*")

        conv = self.get_conversation(conversation_id, user_id)
        if conv is None:
            raise ConversationNotFoundError(
                f"Failed to retrieve updated conversation {conversation_id}"
            )
        return conv

    def delete_conversation(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> bool:
        """Delete a conversation (soft delete)."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")

        conv_type = getattr(conv, "conversation_type", None)
        if isinstance(conv_type, str):
            conv_type = conv_type.lower()
        else:
            conv_type = getattr(conv_type, "value", conv_type)

        # For groups, only owner can delete
        if conv_type == ConversationType.GROUP.value:
            if conv.owner_id != user_id:
                raise ConversationAccessDeniedError("Only owner can delete group")

        now = self._get_timestamp()
        self._repo.soft_delete(conversation_id, now)

        # Invalidate cache
        invalidate_pattern(f"conv_data:*int:{conversation_id}:*")

        # For DMs, also remove the lookup entry
        if conv_type == ConversationType.DM.value:
            self._repo.delete_dm_lookup(conversation_id)
            # Invalidate DM lookup cache
            if conv.metadata and "recipient_id" in conv.metadata:
                # We need user_id and recipient_id. If not in metadata, we might need to fetch participants.
                # But soft delete already handles lookup table.
                pass

        logger.debug(f"Deleted conversation {conversation_id}")
        return True

    def leave_conversation(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> bool:
        """Leave a conversation."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")

        conv_type = getattr(conv, "conversation_type", None)
        if isinstance(conv_type, str):
            conv_type = conv_type.lower()
        else:
            conv_type = getattr(conv_type, "value", conv_type)

        if conv_type == ConversationType.DM.value:
            return self.delete_conversation(user_id, conversation_id)

        participant = self._participant_svc.get_participant(conversation_id, user_id)
        if not participant:
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        # If owner is leaving, transfer ownership or delete
        if participant.role == ParticipantRole.OWNER:
            new_owner = self._participant_svc.find_next_owner(conversation_id, user_id)

            if new_owner:
                self._participant_svc.update_role(
                    conversation_id, new_owner, ParticipantRole.OWNER
                )
                self._repo.update_owner(conversation_id, new_owner)
            else:
                return self.delete_conversation(user_id, conversation_id)

        # Remove participant
        self._participant_svc.remove_participant(conversation_id, user_id)

        # If this was the last participant, delete the conversation.
        if not self._participant_svc._repo.get_user_ids_by_conversation(
            conversation_id
        ):
            return self.delete_conversation(user_id, conversation_id)

        return True

    def update_last_message(
        self,
        conversation_id: SnowflakeID,
        message_id: SnowflakeID,
        message_at: int,
        auto_commit: bool = True,
    ) -> None:
        """Update conversation's last message info."""
        self._repo.update(
            conversation_id,
            message_at,
            last_message_id=message_id,
            last_message_at=message_at,
            auto_commit=auto_commit,
        )
