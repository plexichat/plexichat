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

# L6: the rebuild previously did DELETE + RPUSH as two separate commands,
# so a concurrent send (lpush) or a concurrent rebuild could interleave and
# leave a partially-built list that readers then trusted via the llen
# comparison. This script performs DEL + RPUSH* + EXPIRE as ONE atomic
# operation; readers only ever see the old complete list or the new one.
_REBUILD_RECENT_LIST_SCRIPT = """
redis.call('DEL', KEYS[1])
local n = tonumber(ARGV[1])
for i = 2, n + 1 do
    redis.call('RPUSH', KEYS[1], ARGV[i])
end
redis.call('EXPIRE', KEYS[1], ARGV[n + 2])
return 1
"""


def _cached_recent_payload(payload: Any) -> Optional[Message]:
    """Return a cached recent-message payload as a ``Message``, else ``None``.

    The recent-messages list holds ids only; the payloads live under
    ``msg:obj:{id}`` as JSON and rebuild into models solely when their type is
    registered for reconstruction. A payload that comes back as anything else
    (a plain dict) must be treated as a cache MISS: every consumer reads
    attributes off the returned messages, so handing back decoded dicts raises
    ``AttributeError`` instead of returning the page.
    """
    if isinstance(payload, Message):
        return payload
    if not payload:
        return None

    try:
        from src.core.database.cache.serialization import reconstruct_object

        rebuilt = reconstruct_object(payload)
    except Exception as e:
        logger.debug(f"Failed to reconstruct cached message payload: {e}")
        return None

    return rebuilt if isinstance(rebuilt, Message) else None


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
        if not msg_row:
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

        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump the conversation.
        limit = max(0, min(limit, 100))

        if not before_id and not after_id:
            try:
                from src.core.database import get_valkey_client as get_client

                client = get_client()
                if client:
                    list_key = f"msg:recent:{conversation_id}"
                    cached_ids = client.lrange(list_key, 0, limit - 1)
                    if cached_ids:
                        messages = []
                        missing_ids = []
                        for mid in cached_ids:
                            m_obj = _cached_recent_payload(cache_get(f"msg:obj:{mid}"))
                            if m_obj is not None:
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
                    get_valkey_client as get_client,
                    valkey_available,
                )

                client = get_client()
                if client and valkey_available():
                    list_key = f"msg:recent:{conversation_id}"
                    mids = [str(m.id) for m in messages]
                    if not self._rebuild_recent_list_atomic(client, list_key, mids):
                        # Degrade safely to the legacy non-atomic path.
                        client.delete(list_key)
                        if mids:
                            client.rpush(list_key, *mids)
                            client.expire(list_key, 3600)
            except Exception as e:
                logger.debug(f"Failed to populate recent messages cache: {e}")

        return messages

    @staticmethod
    def _rebuild_recent_list_atomic(client, list_key: str, mids: list) -> bool:
        """Rebuild the recent-messages list atomically via Lua when possible.

        Returns True on success; False (after a debug log) when the client
        cannot run scripts so the caller can fall back to the legacy path.
        """
        if not mids:
            return False
        try:
            eval_lua = getattr(client, "eval_lua", None)
            if eval_lua is None:
                return False
            eval_lua(
                _REBUILD_RECENT_LIST_SCRIPT,
                [list_key],
                [len(mids), *mids, 3600],
            )
            return True
        except Exception as e:
            logger.debug(f"Atomic recent-list rebuild failed for {list_key}: {e}")
            return False

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

        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would return every match instead of an empty page.
        limit = max(0, min(limit, 100))
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
