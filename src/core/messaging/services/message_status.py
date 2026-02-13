"""
Message status service - Business logic for delivery/read status.
"""

from typing import Any, Dict, List, Optional

from src.core.database import cached
from ..models import MessageStatus, MessageStatusType
from ..repositories.message_status import MessageStatusRepository
from ..repositories.message import MessageRepository
from ..repositories.participant import ParticipantRepository
from ..exceptions import (
    ConversationAccessDeniedError,
    MessageNotFoundError,
    MessageAccessDeniedError,
)
from .base import BaseService
from .participant import ParticipantService
from .user_settings import UserSettingsService
from src.core.base import SnowflakeID


class MessageStatusService(BaseService):
    """Service for message status operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
        user_settings_service: UserSettingsService,
    ) -> None:
        super().__init__(db)
        self._repo = MessageStatusRepository(db)
        self._message_repo = MessageRepository(db)
        self._participant_repo = ParticipantRepository(db)
        self._participant_svc = participant_service
        self._user_settings_svc = user_settings_service

    def mark_delivered(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> int:
        """Mark messages as delivered (batch operation)."""
        if not message_ids:
            return 0

        now = self._get_timestamp()

        # Filter to messages user can access and didn't author
        valid_ids: List[SnowflakeID] = []
        for msg_id in message_ids:
            msg_row = self._message_repo.get_by_id(msg_id)
            if not msg_row or msg_row["author_id"] == user_id:
                continue
            if not self._participant_svc.is_participant(msg_row["conversation_id"], user_id):
                continue
            valid_ids.append(msg_id)

        if not valid_ids:
            return 0

        status_id = self._generate_id()
        return self._repo.batch_mark_delivered(
            user_id, valid_ids, now, status_id
        )

    def mark_read(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        up_to_message_id: Optional[SnowflakeID] = None,
    ) -> int:
        """Mark messages as read (batch operation)."""
        # Get participant to check current last_read_message_id
        participant = self._participant_svc.get_participant(conversation_id, user_id)
        if not participant:
            # Fallback to is_participant check if get_participant fails (though it shouldn't for a member)
            if not self._participant_svc.is_participant(conversation_id, user_id):
                raise ConversationAccessDeniedError("Not a participant in this conversation")
            last_read_id = 0
        else:
            last_read_id = participant.last_read_message_id or 0

        # Determine target message ID
        target_msg_id = up_to_message_id
        if not target_msg_id:
            target_msg_id = self._message_repo.get_max_id_in_conversation(conversation_id)

        # Skip if no new messages to mark
        if not target_msg_id or target_msg_id <= last_read_id:
            return 0

        now = self._get_timestamp()

        # Only update public read statuses if user has read receipts enabled
        user_settings = self._user_settings_svc.get_message_settings(user_id)
        
        count = 0
        if user_settings.read_receipts_enabled:
            status_id = self._generate_id()
            # Pass last_read_id to avoid redundant updates
            count = self._repo.batch_mark_read(
                user_id, conversation_id, target_msg_id, now, status_id, start_from_id=last_read_id
            )
            
            if count > 0:
                # Invalidate reader IDs and status batch cache for ALL users
                try:
                    from src.core.database import invalidate_pattern
                    # Key format: msg_reader_ids:* and msg_status_batch:*
                    invalidate_pattern("msg_reader_ids:*")
                    invalidate_pattern("msg_status_batch:*")
                    
                    # ALSO invalidate the individual message object caches since they contain read counts and status
                    invalidate_pattern("msg:obj:*")
                    # And the recent messages list cache
                    invalidate_pattern(f"msg:recent:{conversation_id}")
                    
                    # ALSO invalidate the message list caches for the API and internal repo
                    self._message_repo.invalidate_conversation_cache(conversation_id)
                except Exception:
                    pass

        # Update participant's last read position (always, for unread counts)
        self._participant_repo.update_last_read(
            conversation_id, user_id, target_msg_id, now
        )
        
        # Invalidate unread counts cache
        try:
            # from src.core.database import invalidate_cached
            self.get_unread_count.invalidate(user_id, conversation_id)  # type: ignore
            self.get_unread_count.invalidate(user_id, None)  # type: ignore
        except Exception:
            pass

        return count

    @cached(ttl=10, prefix="unread_counts")
    def get_unread_count(
        self, user_id: SnowflakeID, conversation_id: Optional[SnowflakeID] = None
    ) -> Dict[SnowflakeID, int]:
        """Get unread message counts (cached)."""
        if conversation_id:
            if not self._participant_svc.is_participant(conversation_id, user_id):
                return {}
            count = self._repo.get_unread_count(user_id, conversation_id)
            return {conversation_id: count}

        return self._repo.get_all_unread_counts(user_id)

    def get_message_status(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[MessageStatus]:
        """Get delivery/read status for a message (sender only)."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        # Security: only the author of the message can view its status details
        if msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Only sender can view message status")

        rows = self._repo.get_all_by_message(message_id)
        return [self._repo.row_to_model(row) for row in rows]

    def get_reader_ids(self, user_id: SnowflakeID, message_id: SnowflakeID) -> List[SnowflakeID]:
        """Get IDs of users who have read a message (sender only)."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            return []
            
        # Security: only the author can see who read their message
        if msg_row["author_id"] != user_id:
            return []
            
        return self._repo.get_reader_ids(message_id)

    @cached(ttl=30, prefix="msg_reader_ids")
    def get_batch_reader_ids(self, user_id: SnowflakeID, message_ids: List[SnowflakeID]) -> Dict[SnowflakeID, List[SnowflakeID]]:
        """Get IDs of users who have read messages (batch, sender only)."""
        if not message_ids:
            return {}
            
        # Get message rows to verify ownership
        msg_rows = self._message_repo.get_batch_by_ids(message_ids)
        
        # Filter for messages where user is the author
        owned_message_ids = [
            row["id"] for row in msg_rows if row["author_id"] == user_id
        ]
        
        if not owned_message_ids:
            return {mid: [] for mid in message_ids}
            
        reader_map = self._repo.get_batch_reader_ids(owned_message_ids)
        
        # Ensure all requested IDs are in the result
        result: Dict[SnowflakeID, List[SnowflakeID]] = {mid: [] for mid in message_ids}
        result.update(reader_map)
        return result

    @cached(ttl=30, prefix="msg_status_batch")
    def get_batch_status_info(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, Dict[str, Any]]:
        """Get status info for multiple messages (batch operation)."""
        if not message_ids:
            return {}

        import utils.logger as logger
        status_map = self._repo.get_batch_for_user(user_id, message_ids)
        counts_map = self._repo.get_batch_counts(message_ids)

        result: Dict[SnowflakeID, Dict[str, Any]] = {}
        for mid in message_ids:
            stats = counts_map.get(mid, {"delivery_count": 0, "read_count": 0})
            
            # Determine overall status: if read_count > 0, it's READ. 
            # If delivery_count > 0, it's DELIVERED. Otherwise it's the user's own status.
            overall_status = status_map.get(mid, MessageStatusType.SENT)
            if stats["read_count"] > 0:
                overall_status = MessageStatusType.READ
            elif stats["delivery_count"] > 0:
                # Only upgrade to DELIVERED if it was just SENT
                if overall_status == MessageStatusType.SENT:
                    overall_status = MessageStatusType.DELIVERED

            result[mid] = {
                "status": overall_status,
                "delivery_count": stats["delivery_count"],
                "read_count": stats["read_count"],
            }
            
            if stats["read_count"] > 0:
                logger.debug(f"Status Info: Message {mid} for user {user_id} has read_count {stats['read_count']}, overall={overall_status}")

        return result

    def create_initial_status(
        self,
        message_id: SnowflakeID,
        user_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Create initial sent status for a message."""
        now = self._get_timestamp()
        status_id = self._generate_id()
        self._repo.create(
            status_id, message_id, user_id, MessageStatusType.SENT, now, auto_commit
        )
