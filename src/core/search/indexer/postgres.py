"""
PostgreSQL search indexer - Standard SQL search for Postgres.
"""

from typing import Optional, Any, List
import json

import utils.logger as logger

from .base import BaseIndexer, IndexerConfig
from ..models import (
    IndexedMessage,
    IndexedUser,
    IndexedServer,
    MessageSearchResult,
    UserSearchResult,
    ServerSearchResult,
)
from ..exceptions import SearchIndexError, SearchBackendError


class PostgresIndexer(BaseIndexer):
    """PostgreSQL search indexer using standard tables and LIKE/tsvector."""

    def __init__(self, db, config: Optional[IndexerConfig] = None):
        super().__init__(config)
        self._db = db
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize search tables."""
        if self._initialized:
            return True

        try:
            # We use standard tables instead of VIRTUAL tables
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS search_messages (
                    message_id BIGINT PRIMARY KEY,
                    content TEXT,
                    author_id BIGINT,
                    author_username TEXT,
                    conversation_id BIGINT,
                    server_id BIGINT,
                    channel_id BIGINT,
                    created_at BIGINT,
                    has_attachments BOOLEAN,
                    has_embeds BOOLEAN,
                    has_links BOOLEAN,
                    mentions TEXT,
                    is_pinned BOOLEAN
                )
            """)

            self._db.execute("""
                CREATE TABLE IF NOT EXISTS search_users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    is_bot BOOLEAN
                )
            """)

            self._db.execute("""
                CREATE TABLE IF NOT EXISTS search_servers (
                    server_id BIGINT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    tags TEXT,
                    category TEXT,
                    member_count INTEGER,
                    is_public BOOLEAN
                )
            """)

            # Add GIN indexes for tsvector if we want full text, 
            # but for now let's just get it working with basic tables.
            
            self._initialized = True
            logger.info("Postgres search indexer initialized")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Postgres indexer: {e}")
            raise SearchBackendError(
                f"Failed to initialize Postgres search: {e}",
                backend="postgres",
                original_error=e,
            )

    def index_message(self, message: IndexedMessage) -> bool:
        try:
            self._ensure_initialized()
            
            mentions_json = json.dumps(message.mentions) if message.mentions else "[]"
            
            self._db.upsert(
                "search_messages",
                ["message_id", "content", "author_id", "author_username", "conversation_id",
                 "server_id", "channel_id", "created_at", "has_attachments", "has_embeds",
                 "has_links", "mentions", "is_pinned"],
                (
                    message.message_id,
                    message.content or "",
                    message.author_id,
                    "",
                    message.conversation_id,
                    message.server_id,
                    message.channel_id,
                    message.created_at,
                    message.has_attachments,
                    message.has_embeds,
                    message.has_links,
                    mentions_json,
                    message.is_pinned
                ),
                ["message_id"]
            )
            return True
        except Exception as e:
            logger.error(f"Failed to index message {message.message_id}: {e}")
            raise SearchIndexError(f"Failed to index message: {e}", item_id=message.message_id)

    def close(self):
        self._initialized = False

    def index_messages_batch(self, messages: List[IndexedMessage]) -> int:
        indexed = 0
        for message in messages:
            try:
                if self.index_message(message):
                    indexed += 1
            except SearchIndexError:
                continue
        return indexed

    def remove_message(self, message_id: int) -> bool:
        try:
            self._ensure_initialized()
            self._db.execute("DELETE FROM search_messages WHERE message_id = ?", (message_id,))
            return True
        except Exception:
            return False

    def search_messages(
        self,
        query: str,
        conversation_ids=None,
        server_ids=None,
        channel_ids=None,
        author_ids=None,
        limit=25,
        offset=0,
    ):
        try:
            self._ensure_initialized()
            if not query or not query.strip():
                return []

            sql = "SELECT * FROM search_messages WHERE content ILIKE ?"
            params: List[Any] = [f"%{query}%"]

            if conversation_ids:
                sql += f" AND conversation_id IN ({','.join(['?']*len(conversation_ids))})"
                params.extend(conversation_ids)
            
            if server_ids:
                sql += f" AND server_id IN ({','.join(['?']*len(server_ids))})"
                params.extend(server_ids)

            if channel_ids:
                sql += f" AND channel_id IN ({','.join(['?']*len(channel_ids))})"
                params.extend(channel_ids)

            if author_ids:
                sql += f" AND author_id IN ({','.join(['?']*len(author_ids))})"
                params.extend(author_ids)

            sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = self._db.fetch_all(sql, tuple(params))
            results = []
            for row in rows:
                results.append(MessageSearchResult(
                    id=row["message_id"],
                    message_id=row["message_id"],
                    content=row["content"],
                    author_id=row["author_id"],
                    conversation_id=row["conversation_id"],
                    server_id=row["server_id"],
                    channel_id=row["channel_id"],
                    created_at=row["created_at"],
                    has_attachments=bool(row["has_attachments"]),
                    is_pinned=bool(row["is_pinned"]),
                    score=1.0
                ))
            return results
        except Exception as e:
            logger.error(f"Postgres search failed: {e}")
            return []

    def update_message(self, message: IndexedMessage) -> bool:
        return self.index_message(message)

    def index_user(self, user: IndexedUser) -> bool:
        try:
            self._ensure_initialized()
            self._db.upsert(
                "search_users",
                ["user_id", "username", "display_name", "is_bot"],
                (user.user_id, user.username, user.display_name, user.is_bot),
                ["user_id"]
            )
            return True
        except Exception:
            return False

    def remove_user(self, user_id: int) -> bool:
        try:
            self._ensure_initialized()
            self._db.execute("DELETE FROM search_users WHERE user_id = ?", (user_id,))
            return True
        except Exception:
            return False

    def search_users(self, query: str, limit=25, offset=0):
        try:
            self._ensure_initialized()
            sql = "SELECT * FROM search_users WHERE username ILIKE ? OR display_name ILIKE ? LIMIT ? OFFSET ?"
            rows = self._db.fetch_all(sql, (f"%{query}%", f"%{query}%", limit, offset))
            return [UserSearchResult(
                id=r["user_id"], user_id=r["user_id"], username=r["username"],
                display_name=r["display_name"], is_bot=bool(r["is_bot"]), score=1.0
            ) for r in rows]
        except Exception:
            return []

    def index_server(self, server: IndexedServer) -> bool:
        try:
            self._ensure_initialized()
            self._db.upsert(
                "search_servers",
                ["server_id", "name", "description", "tags", "category", "member_count", "is_public"],
                (server.server_id, server.name, server.description, " ".join(server.tags), 
                 server.category, server.member_count, server.is_public),
                ["server_id"]
            )
            return True
        except Exception:
            return False

    def remove_server(self, server_id: int) -> bool:
        try:
            self._ensure_initialized()
            self._db.execute(
                "DELETE FROM search_servers WHERE server_id = ?", (server_id,)
            )
            return True
        except Exception:
            return False

    def search_servers(
        self, query: str, category=None, public_only=True, limit=25, offset=0
    ):
        try:
            self._ensure_initialized()
            sql = "SELECT * FROM search_servers WHERE (name ILIKE ? OR description ILIKE ?)"
            params: list[Any] = [f"%{query}%", f"%{query}%"]
            if public_only:
                sql += " AND is_public = TRUE"
            if category:
                sql += " AND category = ?"
                params.append(category)
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._db.fetch_all(sql, tuple(params))
            return [ServerSearchResult(
                id=r["server_id"], server_id=r["server_id"], name=r["name"],
                description=r["description"], category=r["category"],
                tags=r["tags"].split() if r["tags"] else [], 
                member_count=r["member_count"], score=1.0
            ) for r in rows]
        except Exception:
            return []

    def get_stats(self):
        try:
            self._ensure_initialized()
            msg_count = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM search_messages"
            )
            user_count = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM search_users"
            )
            server_count = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM search_servers"
            )
            return {
                "backend": "postgres",
                "message_count": msg_count["count"] if msg_count else 0,
                "user_count": user_count["count"] if user_count else 0,
                "server_count": server_count["count"] if server_count else 0,
                "healthy": True,
            }
        except Exception as e:
            return {"backend": "postgres", "healthy": False, "error": str(e)}

    def _ensure_initialized(self):
        if not self._initialized:
            self.initialize()
