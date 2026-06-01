from typing import Any, Dict, Optional

import utils.logger as logger
from src.utils.encryption import decrypt_data, decrypt_message, is_message_encrypted
from .base import SearchManagerBase
from ..models import IndexedMessage


class MessageIndexingMixin(SearchManagerBase):
    def index_message(
        self,
        message_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._config.get("write_time_indexing", True):
            return

        metadata = metadata or {}

        indexed = IndexedMessage(
            message_id=message_id,
            content=content,
            author_id=metadata.get("author_id", 0),
            conversation_id=metadata.get("conversation_id", 0),
            server_id=metadata.get("server_id"),
            channel_id=metadata.get("channel_id"),
            created_at=metadata.get("created_at", self._get_timestamp()),
            has_attachments=metadata.get("has_attachments", False),
            has_embeds=metadata.get("has_embeds", False),
            has_links="http://" in content or "https://" in content,
            mentions=metadata.get("mentions", []),
            is_pinned=metadata.get("is_pinned", False),
        )

        self._indexer.index_message(indexed)

        now = self._get_timestamp()
        source_updated_at = metadata.get("source_updated_at", 0) or 0
        self._db.upsert(
            "search_message_index",
            [
                "message_id",
                "conversation_id",
                "server_id",
                "channel_id",
                "author_id",
                "indexed_at",
                "updated_at",
                "source_updated_at",
            ],
            (
                message_id,
                indexed.conversation_id,
                indexed.server_id,
                indexed.channel_id,
                indexed.author_id,
                now,
                now,
                source_updated_at,
            ),
            ["message_id"],
            [
                "conversation_id",
                "server_id",
                "channel_id",
                "author_id",
                "updated_at",
                "source_updated_at",
            ],
        )

    def remove_from_index(self, message_id: int) -> None:
        self._indexer.remove_message(message_id)
        self._db.execute(
            "DELETE FROM search_message_index WHERE message_id = ?", (message_id,)
        )

    def reindex_all(self, force: bool = False) -> int:
        if force:
            messages = self._db.fetch_all(
                """SELECT m.id, m.content, m.content_encrypted, m.author_id,
                          m.conversation_id, m.created_at, m.updated_at
                   FROM msg_messages m
                   WHERE m.deleted = 0"""
            )
        else:
            messages = self._db.fetch_all(
                """SELECT m.id, m.content, m.content_encrypted, m.author_id,
                          m.conversation_id, m.created_at, m.updated_at
                   FROM msg_messages m
                   LEFT JOIN search_message_index i ON m.id = i.message_id
                   WHERE m.deleted = 0
                     AND (i.message_id IS NULL
                          OR m.updated_at > COALESCE(i.source_updated_at, 0))"""
            )

        indexed = 0
        for msg in messages:
            plaintext = self._decrypt_message_content(
                msg["content"], msg.get("content_encrypted"), msg["id"]
            )
            self.index_message(
                message_id=msg["id"],
                content=plaintext,
                metadata={
                    "author_id": msg["author_id"],
                    "conversation_id": msg["conversation_id"],
                    "created_at": msg["created_at"],
                    "source_updated_at": msg.get("updated_at", 0) or 0,
                },
            )
            indexed += 1

        if not force:
            stale = self._db.fetch_all(
                """SELECT i.message_id
                   FROM search_message_index i
                   LEFT JOIN msg_messages m ON i.message_id = m.id
                   WHERE m.id IS NULL OR m.deleted = 1"""
            )
            for row in stale:
                self.remove_from_index(row["message_id"])

        return indexed

    def reindex_conversation(self, conversation_id: int, force: bool = False) -> int:
        if not self._messaging:
            return 0

        if force:
            messages = self._db.fetch_all(
                """SELECT id, content, content_encrypted, author_id,
                          conversation_id, created_at, updated_at
                   FROM msg_messages
                   WHERE conversation_id = ? AND deleted = 0""",
                (conversation_id,),
            )
        else:
            messages = self._db.fetch_all(
                """SELECT m.id, m.content, m.content_encrypted, m.author_id,
                          m.conversation_id, m.created_at, m.updated_at
                   FROM msg_messages m
                   LEFT JOIN search_message_index i ON m.id = i.message_id
                   WHERE m.conversation_id = ? AND m.deleted = 0
                     AND (i.message_id IS NULL
                          OR m.updated_at > COALESCE(i.source_updated_at, 0))""",
                (conversation_id,),
            )

        indexed = 0
        for msg in messages:
            plaintext = self._decrypt_message_content(
                msg["content"], msg.get("content_encrypted"), msg["id"]
            )
            self.index_message(
                message_id=msg["id"],
                content=plaintext,
                metadata={
                    "author_id": msg["author_id"],
                    "conversation_id": conversation_id,
                    "created_at": msg["created_at"],
                    "source_updated_at": msg.get("updated_at", 0) or 0,
                },
            )
            indexed += 1

        return indexed

    def _decrypt_message_content(
        self, content: Optional[str], content_encrypted: Optional[str], message_id: int
    ) -> str:
        if not content:
            return ""

        if is_message_encrypted(content):
            try:
                return decrypt_message(content, message_id)
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt message {message_id} for search indexing: {e}"
                )
                return ""

        if content == "[encrypted]" and content_encrypted:
            try:
                return decrypt_data(content_encrypted)
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt legacy message {message_id} for search indexing: {e}"
                )
                return ""

        return content
