"""
Last chat service - Tracks and restores the user's last active conversation.

Provides functionality to save, retrieve, and restore the last conversation
a user was viewing, enabling seamless session continuity across reconnects
and page refreshes.
"""

import time
from typing import Optional, Dict, Any, List

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id
from src.core.database import cache_get, cache_set, cache_delete

# ``user_last_chat`` columns in schema order (see
# ``src/core/chat_tracking/schema.py``). ``fetch_one``/``fetch_all`` may
# return a mapping or a bare tuple depending on the database adapter (see
# docs/admin-hardening.md); tuple rows from ``SELECT *`` follow this order.
_LAST_CHAT_COLUMNS = (
    "id",
    "user_id",
    "conversation_id",
    "last_message_id",
    "scroll_position",
    "updated_at",
)

# ``get_recent_chats`` selects these columns in this exact order.
_RECENT_CHAT_COLUMNS = (
    "conversation_id",
    "accessed_at",
    "unread_count",
    "last_message_at",
    "last_message_id",
)


def _last_chat_row_to_dict(row) -> Dict[str, Any]:
    """Normalise a mapping or tuple ``user_last_chat`` row to a dict."""
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(_LAST_CHAT_COLUMNS, row))


def _recent_chat_row_to_dict(row) -> Dict[str, Any]:
    """Normalise a mapping or tuple recent-chat join row to a dict."""
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(_RECENT_CHAT_COLUMNS, row))


class LastChatService:
    """Service for managing user's last active chat state."""

    CACHE_TTL = 86400  # 24 hours
    MAX_HISTORY = 10  # Max recent chats tracked per user

    def __init__(self, db, participant_svc=None):
        self._db = db
        self._participant_svc = participant_svc

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _safe_int(value, default=None):
        """Coerce a value to int, returning default on failure.

        Handles legacy data where IDs may be stored as strings.
        """
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def save_last_chat(
        self,
        user_id: int,
        conversation_id: int,
        last_message_id: Optional[int],
        scroll_position: int = 0,
    ) -> Dict[str, Any]:
        """
        Save the user's last active chat state.

        Args:
            user_id: ID of the user
            conversation_id: ID of the conversation
            last_message_id: ID of the last visible message
            scroll_position: Optional scroll position for restoration

        Returns:
            Saved state record dict
        """
        now = self._get_timestamp()

        # Authorization MUST fail closed. This check used to be guarded by
        # ``if self._participant_svc and ...``: when no participation checker
        # was available (messaging module not initialised, or a manager
        # implementation without ``_participant_svc``), it was silently skipped
        # and any user could point their last-chat state at a conversation they
        # do not belong to (poisoning recent-chat/last-message restoration with
        # an arbitrary conversation/message id). Every other messaging write
        # path denies when the checker is missing (see forwarding.py). Mirror
        # that policy: no checker => no save.
        if not self._participant_svc:
            raise PermissionError("Cannot verify conversation participation")
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise PermissionError("Not a participant in this conversation")

        # Upsert the last chat record
        existing = self._db.fetch_one(
            "SELECT id FROM user_last_chat WHERE user_id = ?",
            (user_id,),
        )

        if existing:
            self._db.execute(
                """UPDATE user_last_chat
                   SET conversation_id = ?, last_message_id = ?, scroll_position = ?,
                       updated_at = ?
                   WHERE user_id = ?""",
                (conversation_id, last_message_id, scroll_position, now, user_id),
            )
        else:
            record_id = self._generate_id()
            self._db.execute(
                """INSERT INTO user_last_chat
                   (id, user_id, conversation_id, last_message_id, scroll_position, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    user_id,
                    conversation_id,
                    last_message_id,
                    scroll_position,
                    now,
                ),
            )

        # Save to Valkey with TTL
        cache_key = f"last_chat:{user_id}"
        cache_data = {
            "conversation_id": conversation_id,
            "last_message_id": last_message_id,
            "scroll_position": scroll_position,
            "updated_at": now,
        }
        cache_set(cache_key, cache_data, ttl=self.CACHE_TTL)

        logger.debug(
            f"Saved last chat for user {user_id}: conversation {conversation_id}"
        )
        self._add_to_history(user_id, conversation_id)
        result = self.get_last_chat(user_id)
        return result if result is not None else {}

    def get_last_chat(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get the user's last active chat state.

        Args:
            user_id: ID of the user

        Returns:
            Last chat state dict or None if not found
        """
        # Try cache first
        cache_key = f"last_chat:{user_id}"
        cached = cache_get(cache_key)
        if cached:
            # Coerce legacy string IDs to int for type compatibility
            conv_id = self._safe_int(cached.get("conversation_id"))
            msg_id = self._safe_int(cached.get("last_message_id"))
            scroll_pos = self._safe_int(cached.get("scroll_position"), default=0)
            # Update cached dict with coerced values
            cached["conversation_id"] = conv_id
            cached["last_message_id"] = msg_id
            cached["scroll_position"] = scroll_pos
            # Authorization must fail CLOSED, exactly like save_last_chat.
            # A missing participation checker is not a grant: returning the
            # cached state when we cannot prove membership would expose a
            # conversation the user was removed from during the cache TTL.
            if conv_id:
                allowed = (
                    bool(self._participant_svc.is_participant(conv_id, user_id))
                    if self._participant_svc
                    else False
                )
                if allowed:
                    return cached
                cache_delete(cache_key)
                return None
            # A state row with no conversation cannot be authorized at all.
            cache_delete(cache_key)
            return None

        # Fall back to database
        row = self._db.fetch_one(
            "SELECT * FROM user_last_chat WHERE user_id = ?",
            (user_id,),
        )
        if not row:
            return None

        data = _last_chat_row_to_dict(row)

        # Verify user is still a participant, failing closed when the checker
        # is unavailable (same contract as the cache-hit path above and as
        # save_last_chat).  A stale row must not become readable just because
        # the messaging module failed to initialise.
        conv_id = data.get("conversation_id")
        if conv_id:
            allowed = (
                bool(self._participant_svc.is_participant(conv_id, user_id))
                if self._participant_svc
                else False
            )
            if not allowed:
                # Clean up stale record
                self._db.execute(
                    "DELETE FROM user_last_chat WHERE user_id = ?",
                    (user_id,),
                )
                return None
        else:
            # No conversation id means the state cannot be authorized.
            self._db.execute(
                "DELETE FROM user_last_chat WHERE user_id = ?",
                (user_id,),
            )
            return None

        # Coerce legacy string IDs to int for type compatibility
        conv_id = self._safe_int(data.get("conversation_id"))
        msg_id = self._safe_int(data.get("last_message_id"))
        scroll_pos = self._safe_int(data.get("scroll_position"), default=0)

        result = {
            "conversation_id": conv_id,
            "last_message_id": msg_id,
            "scroll_position": scroll_pos,
            "updated_at": data.get("updated_at"),
        }

        # Cache it
        cache_set(cache_key, result, ttl=self.CACHE_TTL)

        return result

    def _conversation_read_allowed(self, user_id: int, conversation_id: int) -> bool:
        """Whether messages in the server channel behind ``conversation_id`` may be read.

        ``is_participant`` grants every member of a server participation in all
        of its channel conversations, so it is NOT a read boundary. The normal
        channel message listing path additionally requires the per-channel
        ``messages.read`` grant. A recent-chat row carries unread state and the
        last-message id/at for that conversation, so returning it for a private
        channel a member cannot read leaks activity in that channel. Resolve the
        backing channel membership-agnostically (both the channel-id and
        conversation-id forms are accepted on this API) and require a live read
        grant. A genuine DM/group has no backing channel and needs no extra
        gate. Fails closed whenever the channel or its permission cannot be
        resolved.
        """
        try:
            import src.api as api

            servers = api.get_servers()
        except Exception:
            return False
        if servers is None:
            # The backing-channel lookup cannot be evaluated without the
            # servers module. A private-channel alias can exist in the DB
            # independently of that module, so skipping the gate here would
            # leak a denied channel's unread count and last-message metadata.
            # Deny instead (the unwired-dependency invariant in
            # src/tests/api/test_unwired_dependency_fails_closed.py).
            return False
        if getattr(servers, "_setup_complete", True) is False:
            # Present but uninitialized: no server-channel store exists in
            # this runtime, so a DM/group is the only possible conversation.
            return True  # no server store: only DM/group conversations exist

        channel_id = None
        server_id = None
        channel_exists = getattr(servers, "channel_exists", None)
        if callable(channel_exists):
            try:
                if channel_exists(conversation_id):
                    channel_id = conversation_id
            except Exception:
                return False
        if channel_id is None:
            reverse = getattr(servers, "get_channel_by_conversation", None)
            if callable(reverse):
                try:
                    info = reverse(conversation_id)
                except Exception:
                    return False
                if isinstance(info, dict):
                    channel_id = info.get("id")
                    server_id = info.get("server_id")
        if channel_id is None:
            return True  # genuine DM/group conversation

        if server_id is None:
            get_channel = getattr(servers, "get_channel", None)
            if not callable(get_channel):
                return False
            try:
                channel = get_channel(channel_id, user_id)
            except Exception:
                return False
            if channel is None:
                return False
            server_id = (
                channel.get("server_id")
                if isinstance(channel, dict)
                else getattr(channel, "server_id", None)
            )
        if not server_id:
            return False

        has_permission = getattr(servers, "has_permission", None)
        if not callable(has_permission):
            return False
        try:
            return bool(has_permission(user_id, server_id, "messages.read", channel_id))
        except Exception:
            return False

    def get_recent_chats(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the user's recent chat history.

        Returns conversations ordered by most recently accessed,
        with the last message preview if available.
        """
        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump the whole table.
        limit = max(0, min(limit, self.MAX_HISTORY))

        # Fail closed: a recent-chat row can only be returned after proving the
        # viewer still participates in its conversation.  With no checker that
        # proof is impossible, so an unavailable messaging module must not turn
        # this into an unchecked history dump (mirrors save_last_chat and
        # get_last_chat, which both deny when the checker is missing).
        checker = self._participant_svc
        if not checker:
            return []

        rows = self._db.fetch_all(
            """SELECT h.conversation_id, h.accessed_at, h.unread_count,
                      c.last_message_at, c.last_message_id
               FROM user_recent_chats h
               LEFT JOIN msg_conversations c ON h.conversation_id = c.id
               WHERE h.user_id = ?
               ORDER BY h.accessed_at DESC
               LIMIT ?""",
            (user_id, limit),
        )

        # Resolve participation for the whole page in one bounded batch.  The
        # old loop issued a separate is_participant call per row, so the query
        # count grew with ``limit`` even though the page is capped at ten.  The
        # bulk helper preserves is_participant's full decision tree (direct
        # row, metadata / srv_channels server fallback, server membership) and
        # writes the same per-pair cache entries, so this is a pure batching
        # change with no relaxation of the authz gate.  Test doubles and older
        # manager implementations that only expose is_participant keep the
        # original per-row path.
        normalized = [_recent_chat_row_to_dict(row) for row in rows]
        bulk_check = getattr(checker, "are_participants_bulk", None)
        if bulk_check:
            allowed = bulk_check(
                [data.get("conversation_id") for data in normalized], user_id
            )
        else:
            allowed = None

        results = []
        for data in normalized:
            # Verify user is still a participant.  A row without a
            # conversation id cannot be authorized and is dropped.
            conv_id = data.get("conversation_id")
            if not conv_id:
                continue
            if bulk_check is not None:
                try:
                    permitted = allowed.get(int(conv_id), False)
                except (TypeError, ValueError):
                    permitted = False
            else:
                permitted = checker.is_participant(conv_id, user_id)
            if not permitted:
                continue
            # Participation alone does not authorize a server-channel read (see
            # _conversation_read_allowed). A recent-chat row exposes unread state
            # and last-message metadata, so drop conversations the caller cannot
            # read. Fails closed; the existing participation gate is unchanged.
            if not self._conversation_read_allowed(user_id, int(conv_id)):
                continue
            results.append(data)

        return results

    def update_unread_count(
        self, user_id: int, conversation_id: int, count: int
    ) -> None:
        """Update unread message count for a conversation in recent chats."""
        self._db.execute(
            """UPDATE user_recent_chats
               SET unread_count = ?
               WHERE user_id = ? AND conversation_id = ?""",
            (count, user_id, conversation_id),
        )

    def clear_last_chat(self, user_id: int) -> bool:
        """Clear the user's last chat state."""
        self._db.execute(
            "DELETE FROM user_last_chat WHERE user_id = ?",
            (user_id,),
        )
        cache_key = f"last_chat:{user_id}"
        cache_delete(cache_key)
        return True

    def _add_to_history(self, user_id: int, conversation_id: int) -> None:
        """Add a conversation to the user's recent chat history."""
        now = self._get_timestamp()

        existing = self._db.fetch_one(
            "SELECT id FROM user_recent_chats WHERE user_id = ? AND conversation_id = ?",
            (user_id, conversation_id),
        )

        if existing:
            self._db.execute(
                """UPDATE user_recent_chats
                   SET accessed_at = ?, unread_count = 0
                   WHERE user_id = ? AND conversation_id = ?""",
                (now, user_id, conversation_id),
            )
        else:
            # Check if we need to evict the oldest entry
            count_row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM user_recent_chats WHERE user_id = ?",
                (user_id,),
            )
            # ``SELECT COUNT(*) ...`` yields a single-column row; read it by
            # position when the adapter returns tuples instead of mappings
            # (see docs/admin-hardening.md).
            if not count_row:
                count = 0
            elif isinstance(count_row, dict):
                count = count_row["count"]
            else:
                count = count_row[0]
            if count >= self.MAX_HISTORY:
                # Remove the oldest entry
                self._db.execute(
                    """DELETE FROM user_recent_chats
                       WHERE user_id = ? AND id NOT IN (
                           SELECT id FROM user_recent_chats
                           WHERE user_id = ?
                           ORDER BY accessed_at DESC
                           LIMIT ?
                       )""",
                    (user_id, user_id, self.MAX_HISTORY - 1),
                )

            record_id = self._generate_id()
            self._db.execute(
                """INSERT INTO user_recent_chats
                   (id, user_id, conversation_id, accessed_at, unread_count)
                   VALUES (?, ?, ?, ?, 0)""",
                (record_id, user_id, conversation_id, now),
            )

    def _generate_id(self) -> int:
        return generate_snowflake_id()
