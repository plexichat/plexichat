"""
Pin service - Business logic for pinned messages.
"""

from typing import Any, Dict, List, Optional

from ..models import Message
from ..repositories.pin import PinRepository
from ..repositories.message import MessageRepository
from ..exceptions import (
    ConversationAccessDeniedError,
    MessageNotFoundError,
    PinLimitError,
    MessageNotPinnedError,
)
from .base import BaseService
from .participant import ParticipantService
from src.core.base import SnowflakeID


class PinService(BaseService):
    """Service for pin operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
    ) -> None:
        super().__init__(db)
        self._repo = PinRepository(db)
        self._message_repo = MessageRepository(db)
        self._participant_svc = participant_service

    def pin_message(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """Pin a message in its conversation."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        if msg_row["deleted"]:
            raise MessageNotFoundError("Message not found")

        conversation_id = msg_row["conversation_id"]

        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        # Check if already pinned
        if self._repo.exists(message_id):
            return True

        # Check pin limit
        max_pins = self._get_config("max_pinned_messages", 50)
        pin_count = self._repo.count_by_conversation(conversation_id)

        if pin_count >= max_pins:
            raise PinLimitError(
                f"Cannot pin more than {max_pins} messages",
                max_pins,
                pin_count,
            )

        now = self._get_timestamp()
        pin_id = self._generate_id()

        self._repo.create(pin_id, conversation_id, message_id, user_id, now)

        return True

    def unpin_message(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """Unpin a message."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        if not self._participant_svc.is_participant(msg_row["conversation_id"], user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        if not self._repo.exists(message_id):
            raise MessageNotPinnedError("Message is not pinned")

        self._repo.delete(message_id)
        return True

    def get_pinned_messages(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> List[Message]:
        """Get all pinned messages in a conversation."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")

        rows = self._repo.get_pinned_messages(conversation_id)

        messages = []
        for row in rows:
            pin_info = {"pinned_by": row["pinned_by"], "pinned_at": row["pinned_at"]}
            msg = self._message_repo.row_to_model(row, pin_info)
            messages.append(msg)

        return messages

    def is_pinned(self, message_id: SnowflakeID) -> bool:
        """Check if a message is pinned."""
        return self._repo.exists(message_id)

    def get_pin_info(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get pin info for a message."""
        return self._repo.get_by_message(message_id)

    def get_batch_pin_info(
        self, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, Dict[str, Any]]:
        """Get pin info for multiple messages (batch operation)."""
        return self._repo.get_batch_by_messages(message_ids)
