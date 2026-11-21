"""
User bookmarks service - Business logic for per-user message bookmarks.

Allows users to bookmark/pin messages for their own reference,
independently of server/channel pins.
"""

import time
import zlib
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

# Dedicated PostgreSQL advisory-lock namespace for per-user quota enforcement.
# A fresh namespace (distinct from the migration lock's 12345 and any other key
# in the tree) means quota locks can never block or be confused with unrelated
# advisory-lock holders.
_BOOKMARK_QUOTA_LOCK_NAMESPACE = 0x424F4B4D  # 'BOKM'


class BookmarkService:
    """Service for managing per-user message bookmarks."""

    MAX_BOOKMARKS_PER_USER = 200
    # Enforce the same ceiling on reads as on writes: a single listing must
    # never be able to dump more rows than a user is allowed to create, and
    # every row triggers a per-message enrichment query.
    MAX_LIST_LIMIT = 200
    MAX_LABEL_LENGTH = 100

    def __init__(self, db, messaging_module=None):
        self._db = db
        self._messaging = messaging_module

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    @staticmethod
    def _quota_lock_key(user_id: int) -> tuple:
        """Two-int PostgreSQL advisory key: (fresh namespace, hashed user id).

        The 32-bit CRC keeps the key inside pg_advisory_xact_lock(int4, int4)'s
        two-argument keyspace. A collision between two user ids would only
        cause harmless extra serialization, never a quota bypass.
        """
        return (
            _BOOKMARK_QUOTA_LOCK_NAMESPACE,
            zlib.crc32(str(user_id).encode("utf-8")),
        )

    def _insert_within_quota(self, sql: str, params: tuple, user_id: int) -> int:
        """Run a self-guarding INSERT and return the number of rows it wrote.

        ``sql`` is an ``INSERT ... SELECT ... WHERE (SELECT COUNT(*)) < cap``
        statement, so the database re-checks the cap atomically with the write.

        SQLite serializes writers, making that single statement sufficient.
        PostgreSQL ``READ COMMITTED`` instead gives each statement a fresh
        snapshot, so two concurrent creates could both observe ``count < cap``
        before either commits. There we take a transaction-scoped advisory lock
        keyed by the user before the guarded INSERT: the second caller blocks
        until the first commits and then sees the new count. The lock is
        released by the commit below (or by rollback on error).
        """
        if getattr(self._db, "type", None) == "postgres":
            self._db.begin_transaction()
            try:
                self._db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)", self._quota_lock_key(user_id)
                )
                cursor = self._db.execute(sql, params)
                rowcount = getattr(cursor, "rowcount", 0)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            return rowcount
        cursor = self._db.execute(sql, params)
        return getattr(cursor, "rowcount", 0)

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

    def _conversation_read_allowed(self, user_id: int, conversation_id: int) -> bool:
        """Whether messages in the server channel behind ``conversation_id`` may be read.

        ``is_participant`` grants every member of a server participation in all
        of its channel conversations, so it is NOT a read boundary. The normal
        channel message listing path additionally requires the per-channel
        ``messages.read`` grant; bookmark listing must do the same or a member
        denied read by a role/channel override can read bookmarked message
        content from a private channel. Resolve the backing channel
        membership-agnostically (both the channel-id and conversation-id forms
        are accepted on this API) and require a live read grant. A genuine
        DM/group has no backing channel and needs no extra gate. Fails closed
        whenever the channel or its permission cannot be resolved.
        """
        try:
            import src.api as api

            servers = api.get_servers()
        except Exception:
            return False
        if servers is None or getattr(servers, "_setup_complete", True) is False:
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
        # Participation alone does not authorize a server-channel read (see
        # _conversation_read_allowed). Keep the denial as PermissionError so the
        # route's existing 404 oracle contract is unchanged.
        if not self._conversation_read_allowed(user_id, conversation_id):
            raise PermissionError("No read access to this conversation")

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

        # TOCTOU fix: the advisory COUNT above is only a fast-fail (it can be
        # stale under a concurrent writer). The INSERT itself re-counts in the
        # same statement, so a user can never be pushed past the cap. A denied
        # guarded INSERT reports rowcount 0; PostgreSQL additionally serializes
        # concurrent creates for this user via _insert_within_quota.
        inserted = self._insert_within_quota(
            """INSERT INTO user_bookmarks (id, user_id, message_id, conversation_id, label, created_at)
               SELECT ?, ?, ?, ?, ?, ?
               WHERE (SELECT COUNT(*) FROM user_bookmarks WHERE user_id = ?) < ?""",
            (
                bookmark_id,
                user_id,
                message_id,
                conversation_id,
                label,
                now,
                user_id,
                self.MAX_BOOKMARKS_PER_USER,
            ),
            user_id,
        )
        if inserted <= 0:
            raise ValueError(
                f"Maximum {self.MAX_BOOKMARKS_PER_USER} bookmarks per user"
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
        # Clamp on BOTH sides: SQLite treats LIMIT -1 as *unlimited*, and a
        # huge positive limit bypassed the page cap just as effectively. Each
        # returned row also costs a message-fetch/enrichment query, making an
        # unbounded read a cheap denial-of-service primitive.
        try:
            requested = int(limit)
        except (TypeError, ValueError):
            requested = 0
        limit = max(0, min(requested, self.MAX_LIST_LIMIT))
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

        # Normalize rows once. Enrichment used to call ``get_message`` once per
        # row -- each call paying a message-row query, a participant probe, a
        # pin query and an attachment query. Resolve all message ids in one
        # bounded fan-out instead, keeping the same view gate (a message the
        # caller cannot see is still dropped, never leaked).
        normalized = [
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
            for row in rows
        ]

        # The primary view gate lives in ``self._messaging``. When it is not
        # available the listing must still never expose metadata for messages
        # the caller cannot view, so run every row through the fail-closed
        # probe (message row + participant table) instead of returning the raw
        # bookmark rows unchecked.
        if not self._messaging:
            return [
                data
                for data in normalized
                if self._is_viewable_by(user_id, data["message_id"])
            ]

        # A real bulk resolver returns an id -> message mapping. Anything else
        # (a legacy/stub messaging object that only implements get_message)
        # means we fall back to the per-row path so the listing contract holds.
        messages = None
        bulk = getattr(self._messaging, "get_messages_by_ids", None)
        if callable(bulk):
            try:
                candidate = bulk(user_id, [data["message_id"] for data in normalized])
                if isinstance(candidate, dict):
                    messages = candidate
            except Exception as enrich_error:
                logger.debug(f"bookmark bulk enrichment failed: {enrich_error}")

        results = []
        for data in normalized:
            try:
                if messages is not None:
                    mid = data["message_id"]
                    msg = messages.get(mid)
                    if msg is None:
                        # Ids travel as ints through the bulk resolver; stored
                        # rows may carry the snowflake as a string.
                        try:
                            msg = messages.get(int(mid))
                        except (TypeError, ValueError):
                            msg = None
                else:
                    msg = self._messaging.get_message(user_id, data["message_id"])
                if not msg:
                    # Not viewable (deleted or no longer a participant):
                    # drop rather than leak metadata.
                    continue
                # The bulk resolver's gate is participation-only; a server
                # member denied messages.read on the backing channel must not
                # receive this row's content from an unfiltered listing either.
                if not self._conversation_read_allowed(
                    user_id, data["conversation_id"]
                ):
                    continue
                data["content_preview"] = (msg.content or "")[:200]
                data["author_id"] = msg.author_id
                data["message_created_at"] = msg.created_at
            except Exception as enrich_error:
                # The view gate could not be evaluated. Fail closed: a row
                # whose visibility is unknown must never be returned, or a
                # transient error becomes a metadata leak for messages the
                # caller can no longer view (the invariant documented above).
                # The direct probe is itself fail-closed on missing rows.
                logger.debug(f"bookmark enrichment failed: {enrich_error}")
                if not self._is_viewable_by(user_id, data["message_id"]):
                    continue
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
