"""
Pin service - Business logic for pinned messages.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger

from ..models import Message
from ..repositories.pin import PinRepository, MAX_PIN_FETCH
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
from src.core.database.cache.operations import cache_delete


def _get_servers_module():
    """Return the servers module without importing the whole API app.

    Mirrors the lazy-resolution strategy in services/message/edit_delete.py
    and services/forwarding.py: prefer the already-configured ``src.api``
    provider, fall back to the light ``src.core.servers`` import.
    """
    import sys

    api_mod = sys.modules.get("src.api")
    if api_mod is not None:
        get_servers = getattr(api_mod, "get_servers", None)
        if get_servers is not None:
            try:
                servers = get_servers()
                if servers:
                    return servers
            except Exception as lookup_error:
                logger.debug(f"servers module lookup failed: {lookup_error}")
    try:
        from src.core import servers

        return servers
    except ImportError:
        return None


def _can_view_conversation_messages(user_id, conversation_id) -> bool:
    """Fail closed when a conversation is backed by a channel the caller can't view.

    ``ParticipantService.is_participant`` grants every member of a server
    participation in its channel conversations, independent of the per-channel
    ``channels.view`` permission. Pin/unpin mutate message state and drive pin
    broadcasts/pin listings, so a member denied view on a private channel must
    not be able to pin or unpin its messages by addressing the backing
    conversation id directly at the *service* layer (which bypasses the route's
    channel-view gate). Resolve the backing channel membership-agnostically and
    require a live view grant on it. A conversation with no backing channel is a
    genuine DM/group and stays gated by participation alone.
    """
    servers = _get_servers_module()
    if servers is None:
        # The backing-channel lookup cannot be evaluated without the servers
        # module. Deny instead of skipping the gate: an unwired dependency is
        # exactly the state in which the reverse lookup cannot prove the
        # absence of a private-channel alias (the unwired-dependency invariant
        # in src/tests/api/test_unwired_dependency_fails_closed.py).
        return False
    reverse = getattr(servers, "get_channel_by_conversation", None)
    if not callable(reverse):
        return True  # legacy/stub store: only the participation gate is available
    try:
        info = reverse(int(conversation_id))
    except Exception:
        return False  # unproven alias must never grant access
    if info is None:
        return True  # DM/group conversation
    if isinstance(info, dict):
        channel_id = info.get("id")
    else:
        channel_id = getattr(info, "id", None)
    if channel_id is None:
        return False
    get_channel = getattr(servers, "get_channel", None)
    if not callable(get_channel):
        return False
    try:
        return get_channel(int(channel_id), int(user_id)) is not None
    except Exception:
        return False


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
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

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

        # The message object cache (msg:obj:{id}) stores the hydrated
        # Message with its pin state; stale entries made the post-pin
        # broadcast carry pinned=false for up to an hour (same contract as
        # the edit/delete paths, which delete before re-hydrating).
        cache_delete(f"msg:obj:{message_id}")

        return True

    def unpin_message(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """Unpin a message."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        if not self._participant_svc.is_participant(
            msg_row["conversation_id"], user_id
        ):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        if not self._repo.exists(message_id):
            raise MessageNotPinnedError("Message is not pinned")

        self._repo.delete(message_id)

        # Mirror the pin path: drop the stale cached message object so the
        # unpin broadcast reports pinned=false immediately.
        cache_delete(f"msg:obj:{message_id}")

        return True

    def get_pinned_messages(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        limit: int = MAX_PIN_FETCH,
    ) -> List[Message]:
        """Get the most recently pinned messages in a conversation (bounded)."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        rows = self._repo.get_pinned_messages(conversation_id, limit)

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
