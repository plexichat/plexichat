from typing import Any, Callable, Dict, List, Optional

import utils.logger as logger
from src.utils.encryption import decrypt_data, decrypt_message, is_message_encrypted
from .base import SearchManagerBase
from ..models import IndexedMessage

# Reindex streams messages through memory in fixed-size pages: select one
# page, decrypt it, hand it to the indexer, drop the plaintext, then move on.
# No stage ever holds more than one page worth of plaintext, so a full
# rebuild no longer materializes every message's content in memory at once
# (the original bulk-mass-decrypt design).
#
# The page size is a memory/throughput trade-off, so it is configurable via
# the "search.reindex_page_size" config key (this constant is only the
# fallback default when the key is absent).
DEFAULT_REINDEX_PAGE_SIZE = 200
# Kept as the historical name for callers that imported the constant; it
# tracks DEFAULT_REINDEX_PAGE_SIZE.
REINDEX_PAGE_SIZE = DEFAULT_REINDEX_PAGE_SIZE


class ReindexCancelled(Exception):
    """Raised internally when a reindex run is asked to stop."""


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

        server_id = metadata.get("server_id")
        channel_id = metadata.get("channel_id")
        if server_id is None and channel_id is None and metadata.get("conversation_id"):
            # Scoped searches filter on server_id/channel_id in the index
            # backend; resolve them from the owning channel so indexed
            # documents are actually reachable by scoped queries.
            channel_row = self._db.fetch_one(
                "SELECT id AS channel_id, server_id FROM srv_channels "
                "WHERE conversation_id = ? LIMIT 1",
                (metadata.get("conversation_id"),),
            )
            if channel_row:
                channel_id = channel_row["channel_id"]
                server_id = channel_row["server_id"]

        indexed = IndexedMessage(
            message_id=message_id,
            content=content,
            author_id=metadata.get("author_id", 0),
            author_username=metadata.get("author_username", ""),
            conversation_id=metadata.get("conversation_id", 0),
            server_id=server_id,
            channel_id=channel_id,
            created_at=metadata.get("created_at", self._get_timestamp()),
            has_attachments=metadata.get("has_attachments", False),
            attachment_types=metadata.get("attachment_types", []),
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

    _REINDEX_SELECT = """
        SELECT m.id, m.content, m.content_encrypted, m.author_id,
               u.username AS author_username,
               m.conversation_id, m.created_at, m.updated_at,
               ch.id AS channel_id, ch.server_id AS server_id
        FROM msg_messages m
        LEFT JOIN auth_users u ON u.id = m.author_id
        LEFT JOIN srv_channels ch ON ch.conversation_id = m.conversation_id
    """

    def reindex_all(
        self,
        force: bool = False,
        page_size: Optional[int] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """Rebuild the message search index WITHOUT bulk plaintext exposure.

        Messages are processed in bounded pages: one SELECT page is fetched,
        decrypted, indexed, and released before the next page is selected.
        Plaintext exists only for the current page (and one batch handed to
        the indexer), never for the whole table.

        Args:
            force: Re-evaluate every message instead of only changed ones.
            page_size: Rows per page (bounds reindex memory usage). When
                omitted, the "search.reindex_page_size" config key is used,
                falling back to DEFAULT_REINDEX_PAGE_SIZE.
            should_cancel: Polled between pages; return True to stop cleanly.
            on_progress: Called after each page with (indexed_so_far, pages_done).

        Returns:
            Number of messages indexed.
        """
        if page_size is None:
            page_size = int(
                self._config.get("reindex_page_size", DEFAULT_REINDEX_PAGE_SIZE) or 0
            )
        page_size = max(1, int(page_size))
        if force:
            where = "WHERE m.deleted = 0"
        else:
            where = """
                LEFT JOIN search_message_index i ON m.id = i.message_id
                WHERE m.deleted = 0
                  AND (i.message_id IS NULL
                       OR m.updated_at > COALESCE(i.source_updated_at, 0))"""

        indexed = 0
        pages = 0
        offset = 0
        while True:
            if should_cancel is not None and should_cancel():
                raise ReindexCancelled()

            page = self._db.fetch_all(
                self._REINDEX_SELECT + " " + where + " LIMIT ? OFFSET ?",
                (page_size, offset),
            )
            if not page:
                break
            offset += len(page)

            # Decrypt the page, hand it to the indexer in one batch, then
            # let both go out of scope. The plaintext lives only here.
            batch: List[IndexedMessage] = []
            batch_meta: List[Dict[str, Any]] = []
            for msg in page:
                plaintext = self._decrypt_message_content(
                    msg["content"],
                    msg.get("content_encrypted"),
                    msg["id"],
                    conversation_id=msg["conversation_id"],
                )
                batch.append(
                    IndexedMessage(
                        message_id=msg["id"],
                        content=plaintext,
                        author_id=msg["author_id"],
                        author_username=msg.get("author_username", ""),
                        conversation_id=msg["conversation_id"],
                        server_id=msg.get("server_id"),
                        channel_id=msg.get("channel_id"),
                        created_at=msg["created_at"],
                        has_attachments=False,
                        attachment_types=[],
                        has_embeds=False,
                        has_links="http://" in plaintext or "https://" in plaintext,
                        mentions=[],
                        is_pinned=False,
                    )
                )
                batch_meta.append(
                    {
                        "message_id": msg["id"],
                        "author_id": msg["author_id"],
                        "conversation_id": msg["conversation_id"],
                        "server_id": msg.get("server_id"),
                        "channel_id": msg.get("channel_id"),
                        "created_at": msg["created_at"],
                        "source_updated_at": msg.get("updated_at", 0) or 0,
                    }
                )
                del plaintext

            self._index_messages_batch_with_bookkeeping(batch, batch_meta)
            indexed += len(batch_meta)
            pages += 1

            if on_progress is not None:
                try:
                    on_progress(indexed, pages)
                except Exception:
                    pass

            if len(page) < page_size:
                break

        # Purge deleted/missing messages from the index in BOTH modes so a
        # forced rebuild cannot resurrect soft-deleted content into results.
        stale = self._db.fetch_all(
            """SELECT i.message_id
               FROM search_message_index i
               LEFT JOIN msg_messages m ON i.message_id = m.id
               WHERE m.id IS NULL OR m.deleted = 1"""
        )
        for row in stale:
            self.remove_from_index(row["message_id"])

        return indexed

    def _index_messages_batch_with_bookkeeping(
        self,
        batch: List[IndexedMessage],
        batch_meta: List[Dict[str, Any]],
    ) -> None:
        """Index one decrypted batch and write the search_message_index rows.

        The indexer receives the batch in a single call (its own batching/
        transactional behavior applies); the bookkeeping table is updated in
        the same loop so tracking stays in lockstep with indexed documents.
        """
        try:
            self._indexer.index_messages_batch(batch)
        except Exception:
            # Fall back to per-message indexing so one bad row cannot lose
            # the whole page's bookkeeping either.
            for indexed in batch:
                try:
                    self._indexer.index_message(indexed)
                except Exception as e:
                    logger.warning(
                        f"Failed to index message {indexed.message_id} during reindex: {e}"
                    )

        now = self._get_timestamp()
        for indexed, meta in zip(batch, batch_meta):
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
                    indexed.message_id,
                    indexed.conversation_id,
                    indexed.server_id,
                    indexed.channel_id,
                    indexed.author_id,
                    now,
                    now,
                    meta["source_updated_at"],
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

    def _decrypt_message_content(
        self,
        content: Optional[str],
        content_encrypted: Optional[str],
        message_id: int,
        conversation_id: Optional[int] = None,
    ) -> str:
        if content and is_message_encrypted(content):
            try:
                return decrypt_message(
                    content, message_id, conversation_id=conversation_id
                )
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt message {message_id} for search indexing: {e}"
                )
                return ""

        if (not content or content == "[encrypted]") and content_encrypted:
            try:
                return decrypt_data(content_encrypted)
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt legacy message {message_id} for search indexing: {e}"
                )
                return ""

        return content or ""
