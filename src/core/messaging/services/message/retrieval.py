"""
Message retrieval operations mixin for the MessageService.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_get, cache_set

from ...models import Message
from ...exceptions import ConversationAccessDeniedError
from ..base import BaseService
from .protocol import MessageServiceProtocol


class RetrievalMixin(BaseService, MessageServiceProtocol):
    """Mixin providing message retrieval operations."""

    def get_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> Optional[Message]:
        """Get a single message by ID."""
        cached_msg = cache_get(f"msg:obj:{message_id}")
        if cached_msg:
            from src.core.database.cache.serialization import reconstruct_object

            msg = reconstruct_object(cached_msg)
            if isinstance(msg, Message):
                if self._participant_svc.is_participant(msg.conversation_id, user_id):
                    return msg

        msg_row = self._repo.get_by_id(message_id)
        if not msg_row or msg_row["deleted"]:
            return None

        if not self._participant_svc.is_participant(
            msg_row["conversation_id"], user_id
        ):
            return None

        pin_info = self._pin_repo.get_by_message(message_id)
        msg = self._repo.row_to_model(msg_row, pin_info)

        att_rows = self._attachment_repo.get_by_message(message_id)
        msg.attachments = [self._attachment_repo.row_to_model(row) for row in att_rows]

        cache_set(f"msg:obj:{message_id}", msg, ttl=3600)
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
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        limit = min(limit, 100)

        if not before_id and not after_id:
            try:
                from src.core.database import get_redis_client as get_client

                client = get_client()
                if client:
                    list_key = f"msg:recent:{conversation_id}"
                    cached_ids = client.lrange(list_key, 0, limit - 1)
                    if cached_ids:
                        messages = []
                        missing_ids = []
                        for mid in cached_ids:
                            m_obj = cache_get(f"msg:obj:{mid}")
                            if m_obj:
                                messages.append(m_obj)
                            else:
                                missing_ids.append(mid)
                        if not missing_ids and len(messages) == limit:
                            return messages
                        if (
                            not missing_ids
                            and len(messages) > 0
                            and len(messages) == client.llen(list_key)
                            and len(messages) < limit
                        ):
                            return messages
            except Exception as e:
                logger.debug(f"Recent messages cache check failed: {e}")

        rows = self._repo.get_by_conversation(
            conversation_id, limit, before_id, after_id
        )
        if not rows:
            return []

        message_ids = [row["id"] for row in rows]
        pins_map = self._pin_repo.get_batch_by_messages(message_ids)
        attachments_map = self._attachment_repo.get_batch_by_messages(message_ids)

        messages = []
        for row in rows:
            pin_info = pins_map.get(row["id"])
            msg = self._repo.row_to_model(row, pin_info)
            msg.attachments = attachments_map.get(row["id"], [])
            messages.append(msg)
            cache_set(f"msg:obj:{msg.id}", msg, ttl=3600)

        if not before_id and not after_id:
            try:
                from src.core.database import (
                    get_redis_client as get_client,
                    redis_available,
                )

                client = get_client()
                if client and redis_available():
                    list_key = f"msg:recent:{conversation_id}"
                    client.delete(list_key)
                    if messages:
                        mids = [m.id for m in messages]
                        client.rpush(list_key, *mids)
                        client.expire(list_key, 3600)
            except Exception as e:
                logger.debug(f"Failed to populate recent messages cache: {e}")

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
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        limit = min(limit, 100)
        rows = self._repo.search(conversation_id, query, limit, before_id, after_id)

        if not rows:
            return []

        message_ids = [row["id"] for row in rows]
        pins_map = self._pin_repo.get_batch_by_messages(message_ids)
        attachments_map = self._attachment_repo.get_batch_by_messages(message_ids)

        messages = []
        for row in rows:
            pin_info = pins_map.get(row["id"])
            msg = self._repo.row_to_model(row, pin_info)
            msg.attachments = attachments_map.get(row["id"], [])
            messages.append(msg)

        return messages

    def get_message_raw(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get raw message row (for internal use)."""
        return self._repo.get_by_id(message_id)
