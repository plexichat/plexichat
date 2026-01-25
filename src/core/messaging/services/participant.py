"""
Participant service - Business logic for conversation participants.
"""

from typing import Any, List, Optional

from ..models import Participant, ParticipantRole
from ..repositories.participant import ParticipantRepository
from ..exceptions import (
    ConversationAccessDeniedError,
    ParticipantNotFoundError,
    ParticipantExistsError,
)
from .base import BaseService
from src.core.base import SnowflakeID


class ParticipantService(BaseService):
    """Service for participant operations."""

    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self._repo = ParticipantRepository(db)

    def add_participant(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        role: ParticipantRole,
        auto_commit: bool = True,
    ) -> Participant:
        """Add a participant to a conversation."""
        if self._repo.exists(conversation_id, user_id):
            raise ParticipantExistsError("User is already a participant")

        now = self._get_timestamp()
        part_id = self._generate_id()

        self._repo.create(
            part_id, conversation_id, user_id, role, now, auto_commit=auto_commit
        )

        # Invalidate cache
        self._cache_invalidate((conversation_id, user_id))

        row = self._repo.get_by_conversation_and_user(conversation_id, user_id)
        if row is None:
            raise ParticipantNotFoundError("Failed to create participant")
        return self._repo.row_to_model(row)

    def remove_participant(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Remove a participant from a conversation."""
        self._repo.delete(conversation_id, user_id, auto_commit=auto_commit)
        self._cache_invalidate((conversation_id, user_id))

    def get_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Participant]:
        """Get a participant record."""
        row = self._repo.get_by_conversation_and_user(conversation_id, user_id)
        return self._repo.row_to_model(row) if row else None

    def get_all_participants(
        self, conversation_id: SnowflakeID
    ) -> List[Participant]:
        """Get all participants in a conversation."""
        rows = self._repo.get_all_by_conversation(conversation_id)
        return [self._repo.row_to_model(row) for row in rows]

    def get_participant_ids(
        self, conversation_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Get all participant user IDs (for event routing)."""
        return self._repo.get_user_ids_by_conversation(conversation_id)

    def is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        """Check if user is a participant (with caching and server membership check)."""
        cache_key = (conversation_id, user_id)

        # Check cache first
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        import utils.logger as logger
        
        # Check direct participation
        if self._repo.exists(conversation_id, user_id):
            logger.debug(f"User {user_id} is a direct participant in conversation {conversation_id}")
            self._cache_set(cache_key, True)
            return True

        # Check server membership for server channels
        metadata = self._repo.get_conversation_metadata(conversation_id)
        if metadata:
            server_id = metadata.get("server_id")
            if server_id:
                try:
                    is_member = self._repo.check_server_membership(int(server_id), user_id)
                    if is_member:
                        logger.debug(f"User {user_id} is a participant in conversation {conversation_id} via membership in server {server_id}")
                        self._cache_set(cache_key, True)
                        return True
                    else:
                        logger.warning(f"User {user_id} is NOT a member of server {server_id} associated with conversation {conversation_id}")
                except (TypeError, ValueError) as e:
                    logger.error(f"Error checking server membership for conversation {conversation_id}: {e}")
            else:
                logger.debug(f"Conversation {conversation_id} has metadata but no server_id")
        else:
            logger.debug(f"Conversation {conversation_id} has no metadata")

        logger.warning(f"User {user_id} is NOT a participant in conversation {conversation_id} (direct or server-wide)")
        self._cache_set(cache_key, False)
        return False

    def update_role(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        role: ParticipantRole,
        auto_commit: bool = True,
    ) -> Participant:
        """Update a participant's role."""
        self._repo.update_role(conversation_id, user_id, role, auto_commit=auto_commit)

        row = self._repo.get_by_conversation_and_user(conversation_id, user_id)
        if row is None:
            raise ParticipantNotFoundError("Participant not found")
        return self._repo.row_to_model(row)

    def update_mute(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        muted: bool,
        muted_until: Optional[int] = None,
        auto_commit: bool = True,
    ) -> None:
        """Update participant mute status."""
        self._repo.update_mute(
            conversation_id, user_id, muted, muted_until, auto_commit=auto_commit
        )

    def update_last_read(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        last_read_message_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Update participant's last read position."""
        now = self._get_timestamp()
        self._repo.update_last_read(
            conversation_id, user_id, last_read_message_id, now, auto_commit=auto_commit
        )

    def find_next_owner(
        self, conversation_id: SnowflakeID, exclude_user_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Find next suitable owner when current owner leaves."""
        return self._repo.find_next_owner(conversation_id, exclude_user_id)

    def check_permission(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        required_roles: List[ParticipantRole],
    ) -> bool:
        """Check if user has one of the required roles."""
        participant = self.get_participant(conversation_id, user_id)
        if not participant:
            return False
        return participant.role in required_roles

    def require_permission(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        required_roles: List[ParticipantRole],
        error_message: str = "Permission denied",
    ) -> Participant:
        """Require user to have one of the required roles, raise if not."""
        participant = self.get_participant(conversation_id, user_id)
        if not participant:
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        if participant.role not in required_roles:
            raise ConversationAccessDeniedError(error_message)
        return participant
