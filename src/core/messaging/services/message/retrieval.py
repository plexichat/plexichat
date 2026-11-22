"""
Message retrieval operations mixin for the MessageService.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_get, cache_set

from ...models import Message
from ...pagination import MessagePage
from ...exceptions import ConversationAccessDeniedError
from ..base import BaseService
from .protocol import MessageServiceProtocol

# IN predicates are built from bound-parameter counts (never untrusted text),
# but SQLite/PostgreSQL still cap host parameters. Keep each fan-out chunk at
# or below this ceiling so a large listing degrades to a handful of round
# trips instead of one query per row (an N+1).
_BULK_ID_CHUNK = 500


def _get_servers_module():
    """Return the servers module without importing the whole API app.

    Mirrors the lazy-resolution strategy in edit_delete.py / message_status.py:
    prefer the already-configured ``src.api`` provider, fall back to the light
    ``src.core.servers`` import.
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


def _resolve_authoritative_server_scope(conversation_id) -> Optional[tuple]:
    """Resolve ``(channel_id, server_id)`` from a SERVER conversation id.

    The search route derives its ``messages.read`` server id from the
    client-supplied URL id via ``get_channel(url_id)``/``channel_exists``. That
    id may be the conversation-id alias of a server channel, which those
    channel probes cannot see as a channel, so the route silently treats the
    alias as a DM and SKIPS its per-channel read gate -- a member denied
    ``messages.read`` can then search a private channel's messages simply by
    addressing it through its linked conversation id. Derive the scope here
    from the authoritative conversation id the server stored, which the caller
    cannot spoof, and enforce the gate on that scope. This also covers service
    callers that bypass the route entirely. ``None`` means the conversation
    has no backing server channel (a genuine DM/group) and carries no
    server-scoped read gate.
    """
    servers = _get_servers_module()
    if servers is None:
        # No server module at all: there is no server-channel scope to resolve.
        # A DM-only deployment still searches through the participation gate.
        return None
    if getattr(servers, "_setup_complete", True) is False:
        # Wired but uninitialized: no channel store exists, so a DM/group is
        # the only possible conversation (mirrors the bookmark/recent-chat
        # read gates). Do not over-deny genuine DMs here.
        return None
    lookup = getattr(servers, "get_channel_by_conversation", None)
    if not callable(lookup):
        # The server module IS wired and initialized, so a private-channel
        # conversation-id alias can exist. Without the reverse lookup we cannot
        # tell it apart from a DM; treating "unknown" as "DM" would skip the
        # messages.read gate. Fail closed, as every peer alias gate does.
        raise ConversationAccessDeniedError("Cannot verify channel read access")
    try:
        info = lookup(int(conversation_id))
    except Exception as lookup_error:
        # An unproven lookup is NOT evidence of a missing backing channel. A
        # member denied messages.read must not search a private channel simply
        # because the channel store was unavailable on this request.
        logger.debug(f"conversation backing-channel lookup failed: {lookup_error}")
        raise ConversationAccessDeniedError("Cannot verify channel read access")
    if not info:
        return None  # clean "no backing channel": a genuine DM/group
    if isinstance(info, dict):
        channel_id = info.get("id")
        server_id = info.get("server_id")
    else:
        channel_id = getattr(info, "id", None)
        server_id = getattr(info, "server_id", None)
    if channel_id is None or server_id is None:
        # A backing row with no usable channel/server id cannot prove which
        # grant to check. Never degrade that into "no gate".
        raise ConversationAccessDeniedError("Cannot verify channel read access")
    try:
        return int(channel_id), int(server_id)
    except (TypeError, ValueError) as exc:
        raise ConversationAccessDeniedError("Cannot verify channel read access") from exc


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

    def get_messages_by_ids(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, "Message"]:
        """Load many viewable messages by id with bounded ``IN`` fan-out.

        This is the set-based equivalent of calling :meth:`get_message` once
        per id. List endpoints (e.g. the per-user bookmark listing) used to do
        exactly that, paying a message-row query, a participant probe, a pin
        query and an attachment query *per row*. Here each of those lookups is
        issued once per bounded chunk of ids, and the participant gate is
        deduplicated per conversation, so the query count grows with the
        number of distinct conversations -- not with the number of rows.

        Only messages the caller may view are returned: a row fails the gate
        for the same reasons ``get_message`` returns ``None`` (missing row or
        no longer a participant). The result is keyed by message id, and ids
        with no row / no access are simply absent, so callers never receive a
        message they did not ask for. The bound (500) is applied to every IN
        list built here.
        """
        unique_ids: List[SnowflakeID] = []
        seen = set()
        for mid in message_ids:
            try:
                value = int(mid)
            except (TypeError, ValueError):
                continue
            if value in seen:
                continue
            seen.add(value)
            unique_ids.append(value)
        if not unique_ids:
            return {}

        # 1. Message rows: one IN query per bounded chunk. No `deleted` filter
        # is applied, matching ``MessageRepository.get_by_id`` used by
        # ``get_message`` (deleted tombstones remain readable to participants).
        rows: List[Dict[str, Any]] = []
        for start in range(0, len(unique_ids), _BULK_ID_CHUNK):
            rows.extend(
                self._repo.get_batch_by_ids(unique_ids[start : start + _BULK_ID_CHUNK])
            )
        if not rows:
            return {}

        # 2. Visibility gate, deduplicated per conversation. ``is_participant``
        # is itself cached, so distinct conversations cost one check each
        # rather than one per message.
        participant_cache: Dict[SnowflakeID, bool] = {}
        accessible: List[Dict[str, Any]] = []
        for row in rows:
            conv_id = row.get("conversation_id")
            if conv_id not in participant_cache:
                try:
                    participant_cache[conv_id] = bool(
                        self._participant_svc.is_participant(conv_id, user_id)
                    )
                except Exception:
                    # Fail closed: an error resolving access must never reveal
                    # a message to a caller who may not view it.
                    participant_cache[conv_id] = False
            if participant_cache[conv_id]:
                accessible.append(row)
        if not accessible:
            return {}

        # 3. Pins and attachments: one batched IN query per chunk each.
        accessible_ids = [row["id"] for row in accessible]
        pins_map: Dict[SnowflakeID, Dict[str, Any]] = {}
        attachments_map: Dict[SnowflakeID, List[Dict[str, Any]]] = {}
        for start in range(0, len(accessible_ids), _BULK_ID_CHUNK):
            chunk = accessible_ids[start : start + _BULK_ID_CHUNK]
            pins_map.update(self._pin_repo.get_batch_by_messages(chunk))
            attachments_map.update(self._attachment_repo.get_batch_by_messages(chunk))

        # 4. Rebuild models via the shared converter so decryption/pin state
        # is identical to the single-message path.
        result: Dict[SnowflakeID, "Message"] = {}
        for row in accessible:
            mid = row["id"]
            msg = self._repo.row_to_model(row, pins_map.get(mid))
            msg.attachments = attachments_map.get(mid, [])
            cache_set(f"msg:obj:{mid}", msg, ttl=3600)
            result[mid] = msg
        return result

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

    def get_messages_page(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> MessagePage:
        """Get one keyset page of messages for a participant.

        Kept separate from ``get_messages`` so the cached recent-list fast
        path (which only applies to the first, uncursored page) cannot hide a
        stale page boundary. Pins and attachments are still batch-fetched for
        the rows on this page, so paging deeper adds no per-message queries.
        """
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        limit = max(0, min(int(limit), 100))
        page = self._repo.get_by_conversation_page(conversation_id, limit, cursor)
        if not page.items:
            return MessagePage(items=[], next_cursor=None, has_more=False)

        message_ids = [row["id"] for row in page.items]
        pins_map = self._pin_repo.get_batch_by_messages(message_ids)
        attachments_map = self._attachment_repo.get_batch_by_messages(message_ids)

        messages = []
        for row in page.items:
            msg = self._repo.row_to_model(row, pins_map.get(row["id"]))
            msg.attachments = attachments_map.get(row["id"], [])
            messages.append(msg)

        return MessagePage(
            items=messages,
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

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

        # Authoritative server scope: participants of a server channel are
        # granted by mere server membership, which is NOT a ``messages.read``
        # gate. The search route derives its server id from get_channel(url_id),
        # which returns None for a channel's linked conversation-id alias and
        # silently skips the read grant. Enforce it here against the backing
        # channel/server resolved from this authoritative conversation id,
        # failing closed when the check itself is unproven. A DM/group has no
        # backing channel and is not gated.
        authoritative_scope = _resolve_authoritative_server_scope(conversation_id)
        if authoritative_scope is not None:
            channel_id, server_id = authoritative_scope
            servers = _get_servers_module()
            has_permission = getattr(servers, "has_permission", None) if servers else None
            if not callable(has_permission):
                raise ConversationAccessDeniedError(
                    "Cannot verify channel read access"
                )
            try:
                allowed = bool(
                    has_permission(user_id, server_id, "messages.read", channel_id)
                )
            except Exception:
                allowed = False  # unproven -> fail closed
            if not allowed:
                raise ConversationAccessDeniedError(
                    "Cannot read messages in this channel"
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
