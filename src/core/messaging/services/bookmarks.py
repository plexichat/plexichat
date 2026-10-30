"""
User bookmarks service - Business logic for per-user message bookmarks.

Allows users to bookmark/pin messages for their own reference,
independently of server/channel pins.
"""

import time
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


class BookmarkService:
    """Service for managing per-user message bookmarks."""

    MAX_BOOKMARKS_PER_USER = 200
    MAX_LABEL_LENGTH = 100

    def __init__(self, db, messaging_module=None):
        self._db = db
        self._messaging = messaging_module

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def _channel_owns_conversation(self, channel_id: int, conversation_id: int) -> bool:
        """Return True when ``channel_id`` is a channel backed by ``conversation_id``.

        Server-channel messages are addressed by their *channel* id on the
        client (that is the only id the UI holds) while ``msg_messages``
        stores the channel's separate ``conversation_id``. Both forms are
        accepted, but only for an id that actually maps to where the message
        lives, so this cannot be used to cross conversations.
        """
        try:
            row = self._db.fetch_one(
                "SELECT 1 FROM srv_channels WHERE id = ? AND conversation_id = ?",
                (channel_id, conversation_id),
            )
        except Exception:
            return False
        return row is not None

    def _ensure_bookmark_access(
        self, user_id: int, message_id: int, conversation_id: int
    ) -> None:
        """Validate message existence, conversation match, and membership.

        ``conversation_id`` may be either the message's real conversation id
        (DM, group) or, for server-channel messages, the channel id that
        fronts that conversation.

        Raises ``ValueError`` when the message does not exist (or lives in a
        different conversation than claimed) and ``PermissionError`` when the
        user is not a participant of the target conversation.

        NOTE (M8 oracle contract): callers MUST map both exception types to
        the same HTTP 404 ``{"error": {"code": 404, "message":
        "Message not found"}}`` response. This mirrors the message-GET route
        (``messages_crud/retrieve.py``), which returns 404 — not 403 — when
        ``messaging.get_message`` yields nothing for a non-participant, so
        probing arbitrary message IDs cannot distinguish "nonexistent" from
        "no access". Never surface ``str(e)`` to the client.
        """
        msg_row = self._db.fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ? AND deleted = 0",
            (message_id,),
        )
        if not msg_row:
            raise ValueError("Message not found")
        message_conversation_id = int(
            msg_row["conversation_id"] if isinstance(msg_row, dict) else msg_row[0]
        )
        claimed_id = int(conversation_id)
        if (
            claimed_id != message_conversation_id
            and not self._channel_owns_conversation(claimed_id, message_conversation_id)
        ):
            raise PermissionError("Message is not in the specified conversation")

        # Authorize against the conversation the message really lives in, not
        # the claimed id, so a channel-id target cannot widen the check.
        participant_svc = (
            getattr(self._messaging, "_participant_svc", None)
            if self._messaging
            else None
        )
        if participant_svc is not None:
            allowed = participant_svc.is_participant(message_conversation_id, user_id)
        else:
            # Fallback when the messaging module is unavailable: direct
            # participant-table probe so the check can never fail open.
            row = self._db.fetch_one(
                "SELECT 1 FROM msg_participants WHERE conversation_id = ? "
                "AND user_id = ?",
                (message_conversation_id, user_id),
            )
            allowed = row is not None
        if not allowed:
            raise PermissionError("Not a participant in this conversation")

    def _is_viewable_by(self, user_id: int, message_id: int) -> bool:
        """Return True iff ``user_id`` can currently view ``message_id``.

        Reuses the same view gate as the message retrieval path: when the
        messaging module is available, ``get_message`` already returns None
        for non-participants and missing/deleted rows; otherwise falls back
        to a direct participant-table probe (fail closed) so the check can
        never fail open.
        """
        if self._messaging is not None:
            get_message = getattr(self._messaging, "get_message", None)
            if callable(get_message):
                try:
                    return get_message(user_id, message_id) is not None
                except Exception:
                    return False
        try:
            msg_row = self._db.fetch_one(
                "SELECT conversation_id FROM msg_messages WHERE id = ? AND deleted = 0",
                (message_id,),
            )
        except Exception:
            return False
        if not msg_row:
            return False
        conv_id = (
            msg_row["conversation_id"] if isinstance(msg_row, dict) else msg_row[0]
        )
        participant_svc = (
            getattr(self._messaging, "_participant_svc", None)
            if self._messaging
            else None
        )
        try:
            if participant_svc is not None:
                return bool(participant_svc.is_participant(int(conv_id), user_id))
            row = self._db.fetch_one(
                "SELECT 1 FROM msg_participants WHERE conversation_id = ? "
                "AND user_id = ?",
                (int(conv_id), user_id),
            )
            return row is not None
        except Exception:
            return False

    def _ensure_list_access(self, user_id: int, conversation_id: Optional[int]) -> None:
        """Gate conversation-scoped bookmark listing on participation.

        Raises ``PermissionError`` when the caller is not a participant of
        the requested conversation. Route layers MUST map this to HTTP 404
        (same oracle contract as :meth:`_ensure_bookmark_access`).
        """
        if conversation_id is None:
            return
        participant_svc = (
            getattr(self._messaging, "_participant_svc", None)
            if self._messaging
            else None
        )
        try:
            if participant_svc is not None:
                allowed = participant_svc.is_participant(conversation_id, user_id)
            else:
                row = self._db.fetch_one(
                    "SELECT 1 FROM msg_participants WHERE conversation_id = ? "
                    "AND user_id = ?",
                    (conversation_id, user_id),
                )
                allowed = row is not None
        except Exception:
            allowed = False
        if not allowed:
            raise PermissionError("Not a participant in this conversation")

    def add_bookmark(
        self,
        user_id: int,
        message_id: int,
        conversation_id: int,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Bookmark a message for a user.

        Args:
            user_id: ID of the user bookmarking
            message_id: ID of the message to bookmark
            conversation_id: ID of the conversation the message is in
            label: Optional label for the bookmark

        Returns:
            Bookmark record dict
        """
        # H2 FIX: the caller must be a participant of the conversation the
        # bookmark claims, and the message must actually exist inside that
        # conversation. Without this, arbitrary message/conversation ids could
        # be probed and foreign (possibly private) content referenced.
        self._ensure_bookmark_access(user_id, message_id, conversation_id)

        # Check limit
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM user_bookmarks WHERE user_id = ?",
            (user_id,),
        )
        count = (
            (count_row["count"] if isinstance(count_row, dict) else count_row[0])
            if count_row
            else 0
        )
        if count >= self.MAX_BOOKMARKS_PER_USER:
            raise ValueError(
                f"Maximum {self.MAX_BOOKMARKS_PER_USER} bookmarks per user"
            )

        # Validate label
        if label:
            label = label.strip()[: self.MAX_LABEL_LENGTH]

        # Check if already bookmarked
        existing = self._db.fetch_one(
            "SELECT id FROM user_bookmarks WHERE user_id = ? AND message_id = ?",
            (user_id, message_id),
        )
        if existing:
            # Update label if provided
            if label is not None:
                existing_id = (
                    existing["id"] if isinstance(existing, dict) else existing[0]
                )
                self._db.execute(
                    "UPDATE user_bookmarks SET label = ? WHERE id = ?",
                    (label, existing_id),
                )
            existing_id = existing["id"] if isinstance(existing, dict) else existing[0]
            row = self._db.fetch_one(
                "SELECT * FROM user_bookmarks WHERE id = ?", (existing_id,)
            )
            return dict(row) if row else {}

        now = self._get_timestamp()
        bookmark_id = self._generate_id()

        self._db.execute(
            """INSERT INTO user_bookmarks (id, user_id, message_id, conversation_id, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bookmark_id, user_id, message_id, conversation_id, label, now),
        )

        logger.debug(f"User {user_id} bookmarked message {message_id}")
        row = self._db.fetch_one(
            "SELECT * FROM user_bookmarks WHERE id = ?", (bookmark_id,)
        )
        return dict(row) if row else {}

    def remove_bookmark(self, user_id: int, message_id: int) -> bool:
        """Remove a bookmark for a user."""
        self._db.execute(
            "DELETE FROM user_bookmarks WHERE user_id = ? AND message_id = ?",
            (user_id, message_id),
        )
        return True

    def get_bookmarks(
        self,
        user_id: int,
        conversation_id: Optional[int] = None,
        limit: int = 50,
        before_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get bookmarks for a user, optionally filtered by conversation.

        M8: a conversation-scoped listing requires participation in that
        conversation (raises ``PermissionError``, mapped to 404 by the
        route), and rows pointing at messages the caller can no longer view
        (deleted, or access revoked) are silently dropped so listing can
        never leak inaccessible message metadata.
        """
        # Gate the conversation filter first so probing conversation IDs
        # cannot distinguish "empty" from "no access" (route maps to 404).
        self._ensure_list_access(user_id, conversation_id)
        # Clamp from below: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump every bookmark.
        limit = max(0, limit)
        query = "SELECT * FROM user_bookmarks WHERE user_id = ?"
        params: list = [user_id]

        if conversation_id:
            query += " AND conversation_id = ?"
            params.append(conversation_id)

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))

        # Enrich with message content if messaging module available.
        # M8: drop rows the caller can no longer view (deleted message or
        # revoked membership) instead of leaking their metadata. The view
        # check reuses the message retrieval path's own gate.
        results = []
        for row in rows:
            data = (
                dict(row)
                if isinstance(row, dict)
                else {
                    "id": row[0],
                    "user_id": row[1],
                    "message_id": row[2],
                    "conversation_id": row[3],
                    "label": row[4],
                    "created_at": row[5],
                }
            )
            if self._messaging:
                try:
                    msg = self._messaging.get_message(user_id, data["message_id"])
                    if not msg:
                        # Not viewable (deleted or no longer a participant):
                        # drop rather than leak metadata.
                        continue
                    data["content_preview"] = (msg.content or "")[:200]
                    data["author_id"] = msg.author_id
                    data["message_created_at"] = msg.created_at
                except Exception:
                    # Transient enrichment failure: keep the row without a
                    # preview rather than hide a legitimate bookmark.
                    pass
            results.append(data)
        return results

    def get_bookmark_count(self, user_id: int) -> int:
        """Get the number of bookmarks for a user."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM user_bookmarks WHERE user_id = ?",
            (user_id,),
        )
        return (row["count"] if isinstance(row, dict) else row[0]) if row else 0

    def is_bookmarked(self, user_id: int, message_id: int) -> bool:
        """Check if a message is bookmarked by a user."""
        row = self._db.fetch_one(
            "SELECT 1 FROM user_bookmarks WHERE user_id = ? AND message_id = ?",
            (user_id, message_id),
        )
        return row is not None
