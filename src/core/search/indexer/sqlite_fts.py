"""
SQLite FTS5 indexer - Full-text search using SQLite FTS5.

This is the default indexer that works out of the box with no external dependencies.
"""

import json
import time
from typing import List, Dict, Any, Optional

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


class SQLiteFTS5Indexer(BaseIndexer):
    """SQLite FTS5 full-text search indexer."""
    
    def __init__(self, db, config: Optional[IndexerConfig] = None):
        super().__init__(config)
        self._db = db
        self._initialized = False
    
    def initialize(self) -> bool:
        """Initialize FTS5 tables."""
        if self._initialized:
            return True
        
        try:
            self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS search_messages_fts USING fts5(
                    message_id UNINDEXED,
                    content,
                    author_id UNINDEXED,
                    author_username,
                    conversation_id UNINDEXED,
                    server_id UNINDEXED,
                    channel_id UNINDEXED,
                    created_at UNINDEXED,
                    has_attachments UNINDEXED,
                    has_embeds UNINDEXED,
                    has_links UNINDEXED,
                    mentions UNINDEXED,
                    is_pinned UNINDEXED,
                    tokenize='porter unicode61'
                )
            """)
            
            self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS search_users_fts USING fts5(
                    user_id UNINDEXED,
                    username,
                    display_name,
                    is_bot UNINDEXED,
                    tokenize='porter unicode61'
                )
            """)
            
            self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS search_servers_fts USING fts5(
                    server_id UNINDEXED,
                    name,
                    description,
                    tags,
                    category UNINDEXED,
                    member_count UNINDEXED,
                    is_public UNINDEXED,
                    tokenize='porter unicode61'
                )
            """)
            
            self._initialized = True
            logger.info("SQLite FTS5 indexer initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize FTS5 indexer: {e}")
            raise SearchBackendError(
                f"Failed to initialize FTS5: {e}",
                backend="sqlite_fts5",
                original_error=e
            )
    
    def close(self):
        """Close the indexer."""
        self._initialized = False
    
    def index_message(self, message: IndexedMessage) -> bool:
        """Index a single message."""
        try:
            self._ensure_initialized()
            
            self._db.execute(
                "DELETE FROM search_messages_fts WHERE message_id = ?",
                (str(message.message_id),)
            )
            
            mentions_json = json.dumps(message.mentions) if message.mentions else "[]"
            
            self._db.execute(
                """INSERT INTO search_messages_fts 
                   (message_id, content, author_id, author_username, conversation_id,
                    server_id, channel_id, created_at, has_attachments, has_embeds,
                    has_links, mentions, is_pinned)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(message.message_id),
                    message.content or "",
                    str(message.author_id),
                    "",
                    str(message.conversation_id),
                    str(message.server_id) if message.server_id else "",
                    str(message.channel_id) if message.channel_id else "",
                    str(message.created_at),
                    "1" if message.has_attachments else "0",
                    "1" if message.has_embeds else "0",
                    "1" if message.has_links else "0",
                    mentions_json,
                    "1" if message.is_pinned else "0",
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to index message {message.message_id}: {e}")
            raise SearchIndexError(
                f"Failed to index message: {e}",
                item_id=message.message_id
            )
    
    def index_messages_batch(self, messages: List[IndexedMessage]) -> int:
        """Index multiple messages in batch."""
        indexed = 0
        for message in messages:
            try:
                if self.index_message(message):
                    indexed += 1
            except SearchIndexError:
                continue
        return indexed
    
    def remove_message(self, message_id: int) -> bool:
        """Remove a message from the index."""
        try:
            self._ensure_initialized()
            self._db.execute(
                "DELETE FROM search_messages_fts WHERE message_id = ?",
                (str(message_id),)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to remove message {message_id}: {e}")
            return False
    
    def update_message(self, message: IndexedMessage) -> bool:
        """Update an indexed message."""
        return self.index_message(message)
    
    def search_messages(
        self,
        query: str,
        conversation_ids: Optional[List[int]] = None,
        server_ids: Optional[List[int]] = None,
        channel_ids: Optional[List[int]] = None,
        author_ids: Optional[List[int]] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[MessageSearchResult]:
        """Search messages using FTS5."""
        try:
            self._ensure_initialized()
            
            if not query or not query.strip():
                return []
            
            fts_query = self._build_fts_query(query)
            
            sql = """
                SELECT message_id, content, author_id, conversation_id,
                       server_id, channel_id, created_at, has_attachments,
                       is_pinned, rank
                FROM search_messages_fts
                WHERE search_messages_fts MATCH ?
            """
            params: List[Any] = [fts_query]
            
            if conversation_ids:
                placeholders = ",".join("?" * len(conversation_ids))
                sql += f" AND conversation_id IN ({placeholders})"
                params.extend(str(cid) for cid in conversation_ids)
            
            if server_ids:
                placeholders = ",".join("?" * len(server_ids))
                sql += f" AND server_id IN ({placeholders})"
                params.extend(str(sid) for sid in server_ids)
            
            if channel_ids:
                placeholders = ",".join("?" * len(channel_ids))
                sql += f" AND channel_id IN ({placeholders})"
                params.extend(str(chid) for chid in channel_ids)
            
            if author_ids:
                placeholders = ",".join("?" * len(author_ids))
                sql += f" AND author_id IN ({placeholders})"
                params.extend(str(aid) for aid in author_ids)
            
            sql += " ORDER BY rank LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = self._db.fetch_all(sql, tuple(params))
            
            results = []
            for row in rows:
                result = MessageSearchResult(
                    id=int(row["message_id"]),
                    message_id=int(row["message_id"]),
                    content=row["content"] or "",
                    author_id=int(row["author_id"]) if row["author_id"] else 0,
                    conversation_id=int(row["conversation_id"]) if row["conversation_id"] else 0,
                    server_id=int(row["server_id"]) if row["server_id"] else None,
                    channel_id=int(row["channel_id"]) if row["channel_id"] else None,
                    created_at=int(row["created_at"]) if row["created_at"] else 0,
                    has_attachments=row["has_attachments"] == "1",
                    is_pinned=row["is_pinned"] == "1",
                    score=abs(float(row["rank"])) if row["rank"] else 0.0,
                )
                results.append(result)
            
            return results
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def index_user(self, user: IndexedUser) -> bool:
        """Index a user."""
        try:
            self._ensure_initialized()
            
            self._db.execute(
                "DELETE FROM search_users_fts WHERE user_id = ?",
                (str(user.user_id),)
            )
            
            self._db.execute(
                """INSERT INTO search_users_fts 
                   (user_id, username, display_name, is_bot)
                   VALUES (?, ?, ?, ?)""",
                (
                    str(user.user_id),
                    user.username or "",
                    user.display_name or "",
                    "1" if user.is_bot else "0",
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to index user {user.user_id}: {e}")
            return False
    
    def remove_user(self, user_id: int) -> bool:
        """Remove a user from the index."""
        try:
            self._ensure_initialized()
            self._db.execute(
                "DELETE FROM search_users_fts WHERE user_id = ?",
                (str(user_id),)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to remove user {user_id}: {e}")
            return False
    
    def search_users(
        self,
        query: str,
        limit: int = 25,
        offset: int = 0,
    ) -> List[UserSearchResult]:
        """Search users using FTS5."""
        try:
            self._ensure_initialized()
            
            if not query or not query.strip():
                return []
            
            fts_query = self._build_fts_query(query)
            
            rows = self._db.fetch_all(
                """SELECT user_id, username, display_name, is_bot, rank
                   FROM search_users_fts
                   WHERE search_users_fts MATCH ?
                   ORDER BY rank
                   LIMIT ? OFFSET ?""",
                (fts_query, limit, offset)
            )
            
            results = []
            for row in rows:
                result = UserSearchResult(
                    id=int(row["user_id"]),
                    user_id=int(row["user_id"]),
                    username=row["username"] or "",
                    display_name=row["display_name"] or None,
                    is_bot=row["is_bot"] == "1",
                    score=abs(float(row["rank"])) if row["rank"] else 0.0,
                )
                results.append(result)
            
            return results
            
        except Exception as e:
            logger.error(f"User search failed: {e}")
            return []
    
    def index_server(self, server: IndexedServer) -> bool:
        """Index a server."""
        try:
            self._ensure_initialized()
            
            self._db.execute(
                "DELETE FROM search_servers_fts WHERE server_id = ?",
                (str(server.server_id),)
            )
            
            tags_str = " ".join(server.tags) if server.tags else ""
            
            self._db.execute(
                """INSERT INTO search_servers_fts 
                   (server_id, name, description, tags, category, member_count, is_public)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(server.server_id),
                    server.name or "",
                    server.description or "",
                    tags_str,
                    server.category or "",
                    str(server.member_count),
                    "1" if server.is_public else "0",
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to index server {server.server_id}: {e}")
            return False
    
    def remove_server(self, server_id: int) -> bool:
        """Remove a server from the index."""
        try:
            self._ensure_initialized()
            self._db.execute(
                "DELETE FROM search_servers_fts WHERE server_id = ?",
                (str(server_id),)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to remove server {server_id}: {e}")
            return False
    
    def search_servers(
        self,
        query: str,
        category: Optional[str] = None,
        public_only: bool = True,
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerSearchResult]:
        """Search servers using FTS5."""
        try:
            self._ensure_initialized()
            
            if not query or not query.strip():
                return []
            
            fts_query = self._build_fts_query(query)
            
            sql = """
                SELECT server_id, name, description, tags, category, 
                       member_count, is_public, rank
                FROM search_servers_fts
                WHERE search_servers_fts MATCH ?
            """
            params: List[Any] = [fts_query]
            
            if public_only:
                sql += " AND is_public = '1'"
            
            if category:
                sql += " AND category = ?"
                params.append(category)
            
            sql += " ORDER BY rank LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = self._db.fetch_all(sql, tuple(params))
            
            results = []
            for row in rows:
                tags = row["tags"].split() if row["tags"] else []
                result = ServerSearchResult(
                    id=int(row["server_id"]),
                    server_id=int(row["server_id"]),
                    name=row["name"] or "",
                    description=row["description"] or None,
                    category=row["category"] or None,
                    tags=tags,
                    member_count=int(row["member_count"]) if row["member_count"] else 0,
                    score=abs(float(row["rank"])) if row["rank"] else 0.0,
                )
                results.append(result)
            
            return results
            
        except Exception as e:
            logger.error(f"Server search failed: {e}")
            return []
    
    def get_stats(self) -> Dict[str, Any]:
        """Get indexer statistics."""
        try:
            self._ensure_initialized()
            
            msg_count = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM search_messages_fts"
            )
            user_count = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM search_users_fts"
            )
            server_count = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM search_servers_fts"
            )
            
            return {
                "backend": "sqlite_fts5",
                "message_count": msg_count["count"] if msg_count else 0,
                "user_count": user_count["count"] if user_count else 0,
                "server_count": server_count["count"] if server_count else 0,
                "healthy": True,
            }
            
        except Exception as e:
            return {
                "backend": "sqlite_fts5",
                "healthy": False,
                "error": str(e),
            }
    
    def _ensure_initialized(self):
        """Ensure indexer is initialized."""
        if not self._initialized:
            self.initialize()
    
    def _build_fts_query(self, query: str) -> str:
        """Build FTS5 query from user query."""
        query = query.strip()
        
        special_chars = ['"', "'", "(", ")", "*", ":", "-", "+", "^", "~"]
        clean_query = query
        for char in special_chars:
            clean_query = clean_query.replace(char, " ")
        
        terms = [t.strip() for t in clean_query.split() if t.strip()]
        
        if not terms:
            return '""'
        
        if len(terms) == 1:
            return f'"{terms[0]}"*'
        
        return " OR ".join(f'"{term}"*' for term in terms)
