"""
Message repository - Data access for messages.
"""

import re
import time
from typing import Any, Dict, List, Optional

from src.core.database import cached, invalidate_pattern
from ..models import Message, MessageType
from .base import BaseRepository
from src.core.base import SnowflakeID
from src.utils.encryption import decrypt_message, is_message_encrypted, decrypt_data

_MAX_SEARCH_TOKENS = 64
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize_search_words(text: str) -> List[str]:
    """Split text into lowercased, de-duplicated search words (max 64).

    Matches the tokenization used at write time so read-side queries line up
    with the stored blind indexes.
    """
    seen: set = set()
    tokens: List[str] = []
    for word in _TOKEN_RE.findall(text.lower()):
        if word not in seen:
            seen.add(word)
            tokens.append(word)
            if len(tokens) >= _MAX_SEARCH_TOKENS:
                break
    return tokens


def _escape_like(text: str) -> str:
    """Escape LIKE wildcards so user input matches literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MessageRepository(BaseRepository[Message]):
    """Repository for message data access."""

    def create(
        self,
        msg_id: SnowflakeID,
        conversation_id: SnowflakeID,
        author_id: SnowflakeID,
        content: str,
        message_type: MessageType,
        created_at: int,
        reply_to_id: Optional[SnowflakeID] = None,
        content_encrypted: Optional[str] = None,
        content_index: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        webhook_id: Optional[SnowflakeID] = None,
        ratchet_interval_id: Optional[SnowflakeID] = None,
        auto_commit: bool = True,
    ) -> None:
        """Create a new message."""
        if created_at is None:
            created_at = int(time.time() * 1000)
        message_type_value = (
            message_type.value
            if isinstance(message_type, MessageType)
            else str(message_type)
        )
        self._execute(
            """INSERT INTO msg_messages 
               (id, conversation_id, author_id, content, content_encrypted, content_index, message_type, 
                created_at, updated_at, reply_to_id, metadata, webhook_id, ratchet_interval_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                conversation_id,
                author_id,
                content,
                content_encrypted,
                content_index,
                message_type_value,
                created_at,
                created_at,
                reply_to_id,
                self._json_dumps(metadata),
                webhook_id,
                ratchet_interval_id,
            ),
            auto_commit=auto_commit,
        )
        self.invalidate_conversation_cache(conversation_id)

    def get_by_id(self, msg_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get message by ID."""
        return self._fetch_one(
            "SELECT * FROM msg_messages WHERE id = ?",
            (msg_id,),
        )

    def get_batch_by_ids(self, msg_ids: List[SnowflakeID]) -> List[Dict[str, Any]]:
        """Get multiple messages by ID."""
        if not msg_ids:
            return []
        in_clause, params = self._build_in_clause(msg_ids)
        return self._fetch_all(
            f"SELECT * FROM msg_messages WHERE id IN {in_clause}",  # nosec: B608
            params,
        )

    @cached(ttl=60, prefix="messages_list")
    def get_by_conversation(
        self,
        conversation_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Dict[str, Any]]:
        """Get messages from a conversation with cursor pagination.

        Deleted messages are included so clients can render the
        'Message deleted by user' tombstone. The ``deleted`` column drives
        rendering; clients filter/display based on it.
        """
        query = "SELECT * FROM msg_messages WHERE conversation_id = ?"
        params: List[Any] = [conversation_id]

        if before_id:
            query += " AND id < ?"
            params.append(before_id)
            query += " ORDER BY id DESC"
        elif after_id:
            query += " AND id > ?"
            params.append(after_id)
            query += " ORDER BY id ASC"
        else:
            query += " ORDER BY id DESC"

        query += " LIMIT ?"
        params.append(limit)

        return self._fetch_all(query, tuple(params))

    def _get_conv_id(self, msg_id: SnowflakeID) -> Optional[SnowflakeID]:
        """Get conversation ID for a message."""
        row = self._fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ?", (msg_id,)
        )
        return row["conversation_id"] if row else None

    def search(
        self,
        conversation_id: SnowflakeID,
        search_query: str,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Dict[str, Any]]:
        """Search messages in a conversation.

        Plaintext messages match via ``content LIKE``; encrypted messages match
        via the per-token blind indexes written at send/edit time, so substring
        search keeps working without exposing plaintext to the query. Falls back
        to plaintext-only matching on databases that predate the token table.
        """
        query_text = (search_query or "").strip()
        if not query_text:
            return []
        like_pattern = f"%{_escape_like(query_text)}%"

        from src.utils.encryption import blind_index

        token_indexes = [
            blind_index(token, "message_content")
            for token in tokenize_search_words(query_text)
        ]

        if token_indexes:
            placeholders = ", ".join("?" for _ in token_indexes)
            match_clause = (
                "  m.content LIKE ? ESCAPE '\\' "
                "  OR ("
                "    SELECT COUNT(DISTINCT t.token_index) FROM msg_message_tokens t "
                "    WHERE t.message_id = m.id AND t.token_index IN ("
                + placeholders
                + ")"
                "  ) = ?"
            )
            params: List[Any] = [
                conversation_id,
                like_pattern,
                *token_indexes,
                len(token_indexes),
            ]
            try:
                return self._search_matching(
                    conversation_id, match_clause, params, limit, before_id, after_id
                )
            except Exception:
                # Older databases without msg_message_tokens: fall back to
                # plaintext-only matching rather than failing the search.
                pass

        match_clause = "  m.content LIKE ? ESCAPE '\\'"
        params = [conversation_id, like_pattern]
        return self._search_matching(
            conversation_id, match_clause, params, limit, before_id, after_id
        )

    def _search_matching(
        self,
        conversation_id: SnowflakeID,
        match_clause: str,
        params: List[Any],
        limit: int,
        before_id: Optional[SnowflakeID],
        after_id: Optional[SnowflakeID],
    ) -> List[Dict[str, Any]]:
        """Run a search query with shared ordering/pagination, ``m`` aliased."""
        query = (
            "SELECT m.* FROM msg_messages m "
            "WHERE m.conversation_id = ? AND m.deleted = 0 AND (" + match_clause + ")"
        )
        bound: List[Any] = list(params)
        if before_id:
            query += " AND m.id < ?"
            bound.append(before_id)
            query += " ORDER BY m.id DESC"
        elif after_id:
            query += " AND m.id > ?"
            bound.append(after_id)
            query += " ORDER BY m.id ASC"
        else:
            query += " ORDER BY m.id DESC"
        query += " LIMIT ?"
        bound.append(limit)
        return self._fetch_all(query, tuple(bound))

    def content_token_indexes(self, content: str) -> List[str]:
        """Blind-index each searchable word of plaintext content."""
        from src.utils.encryption import blind_index

        return [
            blind_index(token, "message_content")
            for token in tokenize_search_words(content)
        ]

    def set_content_tokens(
        self,
        msg_id: SnowflakeID,
        token_indexes: List[str],
        auto_commit: bool = True,
    ) -> None:
        """Replace the per-token search blind indexes for a message."""
        self._execute(
            "DELETE FROM msg_message_tokens WHERE message_id = ?",
            (msg_id,),
            auto_commit=auto_commit,
        )
        for position, token_index in enumerate(token_indexes):
            self._execute(
                "INSERT OR IGNORE INTO msg_message_tokens "
                "(message_id, token_index, position) VALUES (?, ?, ?)",
                (msg_id, token_index, position),
                auto_commit=auto_commit,
            )

    def update_content(
        self,
        msg_id: SnowflakeID,
        content: str,
        updated_at: int,
        content_index: Optional[str] = None,
        auto_commit: bool = True,
        mark_edited: bool = True,
    ) -> bool:
        """Update message content (edit).

        Returns ``True`` when the stored content actually changed.

        Concurrency semantics remain last-write-wins (no version token is
        enforced), but the write is no longer destructive for no-op edits:
        when the new content equals the stored content the legacy
        ``content_encrypted`` ciphertext is PRESERVED and the ``edited``
        flag is not raised. Callers can additionally suppress the edited
        flag entirely (system operations such as AutoMod tombstoning pass
        ``mark_edited=False``) so system rewrites never masquerade as user
        edits. When the edited flag IS raised, ``edited_at`` is stamped
        with ``updated_at`` so consumers see the real edit time instead of
        silently falling back to a generic last-updated timestamp.
        """
        cid = self._get_conv_id(msg_id)
        existing = self._fetch_one(
            "SELECT content, updated_at, edited_at FROM msg_messages WHERE id = ?",
            (msg_id,),
        )
        content_changed = existing is None or existing["content"] != content
        # Monotonic revision guarantee: the optimistic-lock token is the
        # stored ``edited_at``/``updated_at`` (ms). A rewrite that lands in
        # the SAME millisecond as the prior revision would leave the token
        # unchanged and a stale client token would be accepted as fresh.
        # Bump the revision to strictly exceed any prior value instead —
        # every accepted edit must advance the token.
        if existing is not None:
            prior_revision = max(
                int(existing.get("updated_at") or 0),
                int(existing.get("edited_at") or 0),
            )
            if updated_at <= prior_revision:
                updated_at = prior_revision + 1
        raise_edited = 1 if (content_changed and mark_edited) else 0
        self._execute(
            """UPDATE msg_messages
               SET content = ?,
                   content_encrypted = CASE WHEN ? THEN NULL ELSE content_encrypted END,
                   content_index = ?,
                   updated_at = ?,
                   edited = CASE WHEN ? THEN 1 ELSE edited END,
                   edited_at = CASE WHEN ? THEN ? ELSE edited_at END
               WHERE id = ?""",
            (
                content,
                raise_edited,
                content_index,
                updated_at,
                raise_edited,
                raise_edited,
                updated_at,
                msg_id,
            ),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)
        return content_changed

    def update_metadata(
        self,
        msg_id: SnowflakeID,
        metadata: Optional[Dict[str, Any]],
        updated_at: int,
        auto_commit: bool = True,
    ) -> None:
        cid = self._get_conv_id(msg_id)
        self._execute(
            "UPDATE msg_messages SET metadata = ?, updated_at = ? WHERE id = ?",
            (self._json_dumps(metadata), updated_at, msg_id),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)

    def soft_delete(
        self,
        msg_id: SnowflakeID,
        deleted_at: int,
        user_id: Optional[SnowflakeID] = None,
        auto_commit: bool = True,
    ) -> None:
        """Soft delete a message (rendered as 'Message deleted by user').

        The content is blanked and a ``deleted_by_user`` marker is stored in
        metadata. Rendering is driven by the ``deleted`` flag, never by the
        content text, so a user typing those words is not affected.
        """
        cid = self._get_conv_id(msg_id)
        existing_metadata = {}
        try:
            row = self._fetch_one(
                "SELECT metadata FROM msg_messages WHERE id = ?", (msg_id,)
            )
            if row and row.get("metadata"):
                existing_metadata = self._json_loads(row["metadata"]) or {}
        except Exception:
            pass
        existing_metadata.update(
            {
                "deleted_by_user": True,
                "deleted_by": user_id,
                "deleted_at": deleted_at,
            }
        )
        self._execute(
            """UPDATE msg_messages
               SET deleted = 1, deleted_at = ?, content = '', content_encrypted = NULL,
                   content_index = NULL, metadata = ?, updated_at = ?
               WHERE id = ?""",
            (deleted_at, self._json_dumps(existing_metadata), deleted_at, msg_id),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)

    def hard_delete(self, msg_id: SnowflakeID, auto_commit: bool = True) -> None:
        """Hard delete a message."""
        cid = self._get_conv_id(msg_id)
        self._execute(
            "DELETE FROM msg_messages WHERE id = ?",
            (msg_id,),
            auto_commit=auto_commit,
        )
        if cid:
            self.invalidate_conversation_cache(cid)

    def exists_in_conversation(
        self, msg_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> bool:
        """Check if message exists in conversation."""
        row = self._fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ? AND deleted = 0",
            (msg_id,),
        )
        return row is not None and row["conversation_id"] == conversation_id

    def get_max_id_in_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Get the maximum message ID in a conversation."""
        row = self._fetch_one(
            "SELECT MAX(id) as max_id FROM msg_messages WHERE conversation_id = ? AND deleted = 0",
            (conversation_id,),
        )
        return row["max_id"] if row else None

    def get_previous_id_in_conversation(
        self, conversation_id: SnowflakeID, message_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Get the greatest message ID below ``message_id`` in a conversation.

        Snowflake IDs are not sequential (successive IDs differ by ``1 << 22``
        per millisecond, not ``1``), so ``max_id - 1`` never addresses a real
        message. Query the actual predecessor instead.
        """
        row = self._fetch_one(
            "SELECT MAX(id) as prev_id FROM msg_messages WHERE conversation_id = ? AND id < ? AND deleted = 0",
            (conversation_id, message_id),
        )
        if not row:
            return None
        return row["prev_id"]

    def invalidate_conversation_cache(self, conversation_id: SnowflakeID) -> None:
        """Invalidate all message list caches for a conversation."""
        try:
            # Use wider globs to catch various argument serializations (str:ID vs int:ID)
            # Add leading wildcards to catch keys like 'cache:src.api.routes.messages.get_channel_messages:channel_id:int:...'
            pattern1 = f"*messages_list:*{conversation_id}*"
            pattern2 = f"*messages_api:*{conversation_id}*"

            count1 = invalidate_pattern(pattern1)
            count2 = invalidate_pattern(pattern2)

            total = count1 + count2
            if total > 0:
                from utils.logger import debug

                debug(
                    f"Invalidated {total} message cache keys for conversation {conversation_id}"
                )
        except Exception as e:
            from utils.logger import warning

            warning(f"Failed to invalidate message cache for {conversation_id}: {e}")

    def row_to_model(
        self,
        row: Dict[str, Any],
        pin_info: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Convert database row to Message model."""
        content = row["content"]

        # Decrypt if encrypted (new format with ENC: prefix)
        if is_message_encrypted(content):
            try:
                if content.startswith("ENC:3:"):
                    from src.utils.encryption.channel_ratchet import (
                        ratchet_encryption_licensed,
                    )

                    if not ratchet_encryption_licensed():
                        from utils.logger import warning

                        warning(
                            "MessageRepository._row_to_message: encountered "
                            "ENC:3: ratchet envelope for message %s but the "
                            "v3 channel ratchet is not licensed on this "
                            "instance (free tier or licence lacks "
                            "'channel_ratchet_encryption'). Returning a "
                            "sentinel; this is a licence-downgrade condition, "
                            "not a key corruption error.",
                            row.get("id"),
                        )
                        content = "[unsupported encryption version]"
                    else:
                        content = decrypt_message(
                            content,
                            row["id"],
                            conversation_id=row.get("conversation_id"),
                        )
                else:
                    content = decrypt_message(content, row["id"])
            except Exception:
                content = "[decryption failed]"
        # Legacy: decrypt from content_encrypted field
        elif row.get("content_encrypted") and content == "[encrypted]":
            try:
                content = decrypt_data(row["content_encrypted"])
            except Exception:
                content = "[decryption failed]"

        metadata = self._json_loads(row["metadata"]) if row.get("metadata") else None
        embeds = []
        if metadata and "embeds" in metadata:
            embeds = metadata["embeds"]

        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            author_id=row["author_id"],
            content=content,
            content_encrypted=row.get("content_encrypted"),
            message_type=MessageType(row.get("message_type", "text")),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            edited=bool(row.get("edited", False)),
            edited_at=row.get("edited_at"),
            deleted=bool(row.get("deleted", False)),
            deleted_at=row.get("deleted_at"),
            reply_to_id=row.get("reply_to_id"),
            pinned=pin_info is not None,
            pinned_at=pin_info["pinned_at"] if pin_info else None,
            pinned_by=pin_info["pinned_by"] if pin_info else None,
            metadata=metadata,
            embeds=embeds,
        )
