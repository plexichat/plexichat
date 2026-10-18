"""
PostgreSQL search indexer - Standard SQL search for Postgres.
"""

from typing import Optional, Any, List, Tuple
import json
import base64

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


def _escape_like(text: str) -> str:
    """Escape LIKE/ILIKE metacharacters so user text matches literally.

    Parameterization stops SQL injection but does NOT stop ``%``/``_`` from
    acting as wildcards, so without this a bare ``%`` returns every indexed
    row (docs/admin-hardening.md: "Treat %, _, and \\ literally in search
    queries"). The ``\\`` escape character is declared via ``ESCAPE '\\'``.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
                    is_pinned BOOLEAN,
                    attachment_types TEXT
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

            # Add GIN indexes for tsvector full-text search
            self._db.execute(
                "ALTER TABLE search_messages ADD COLUMN IF NOT EXISTS search_vector tsvector"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_search ON search_messages USING GIN(search_vector)"
            )

            # Create a trigger to update search_vector on insert/update
            self._db.execute("""
                CREATE OR REPLACE FUNCTION messages_search_trigger() RETURNS trigger AS $$
                begin
                  new.search_vector := to_tsvector('english', coalesce(new.content,''));
                  return new;
                end
                $$ LANGUAGE plpgsql
            """)
            self._db.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_messages_search') THEN
                        CREATE TRIGGER trg_messages_search BEFORE INSERT OR UPDATE
                        ON search_messages FOR EACH ROW EXECUTE FUNCTION messages_search_trigger();
                    END IF;
                END $$;
            """)

            # Same for users
            self._db.execute(
                "ALTER TABLE search_users ADD COLUMN IF NOT EXISTS search_vector tsvector"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_search ON search_users USING GIN(search_vector)"
            )
            self._db.execute("""
                CREATE OR REPLACE FUNCTION users_search_trigger() RETURNS trigger AS $$
                begin
                  new.search_vector := setweight(to_tsvector('english', coalesce(new.username,'')), 'A') || 
                                       setweight(to_tsvector('english', coalesce(new.display_name,'')), 'B');
                  return new;
                end
                $$ LANGUAGE plpgsql
            """)
            self._db.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_users_search') THEN
                        CREATE TRIGGER trg_users_search BEFORE INSERT OR UPDATE
                        ON search_users FOR EACH ROW EXECUTE FUNCTION users_search_trigger();
                    END IF;
                END $$;
            """)

            # Same for servers
            self._db.execute(
                "ALTER TABLE search_servers ADD COLUMN IF NOT EXISTS search_vector tsvector"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_servers_search ON search_servers USING GIN(search_vector)"
            )
            self._db.execute("""
                CREATE OR REPLACE FUNCTION servers_search_trigger() RETURNS trigger AS $$
                begin
                  new.search_vector := setweight(to_tsvector('english', coalesce(new.name,'')), 'A') || 
                                       setweight(to_tsvector('english', coalesce(new.description,'')), 'B') ||
                                       setweight(to_tsvector('english', coalesce(new.tags,'')), 'C');
                  return new;
                end
                $$ LANGUAGE plpgsql
            """)
            self._db.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_servers_search') THEN
                        CREATE TRIGGER trg_servers_search BEFORE INSERT OR UPDATE
                        ON search_servers FOR EACH ROW EXECUTE FUNCTION servers_search_trigger();
                    END IF;
                END $$;
            """)

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
            attachment_types_str = (
                " ".join(message.attachment_types) if message.attachment_types else ""
            )

            self._db.upsert(
                "search_messages",
                [
                    "message_id",
                    "content",
                    "author_id",
                    "author_username",
                    "conversation_id",
                    "server_id",
                    "channel_id",
                    "created_at",
                    "has_attachments",
                    "has_embeds",
                    "has_links",
                    "mentions",
                    "is_pinned",
                    "attachment_types",
                ],
                (
                    message.message_id,
                    message.content or "",
                    message.author_id,
                    message.author_username or "",
                    message.conversation_id,
                    message.server_id,
                    message.channel_id,
                    message.created_at,
                    message.has_attachments,
                    message.has_embeds,
                    message.has_links,
                    mentions_json,
                    message.is_pinned,
                    attachment_types_str,
                ),
                ["message_id"],
            )
            return True
        except Exception as e:
            logger.error(f"Failed to index message {message.message_id}: {e}")
            raise SearchIndexError(
                f"Failed to index message: {e}", item_id=message.message_id
            )

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
            self._db.execute(
                "DELETE FROM search_messages WHERE message_id = ?", (message_id,)
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to remove message {message_id} from Postgres index: {e}"
            )
            return False

    def _check_websearch_support(self) -> bool:
        try:
            self._db.fetch_one(
                "SELECT 1 WHERE websearch_to_tsquery('english', 'test') IS NOT NULL"
            )
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

            use_websearch = self._check_websearch_support()
            like = f"%{_escape_like(query)}%"

            if use_websearch:
                sql = """
                    SELECT message_id, content, author_id, author_username,
                           conversation_id, server_id, channel_id,
                           created_at, has_attachments, has_embeds, has_links,
                           is_pinned, attachment_types,
                           ts_rank_cd(search_vector, query) as rank
                    FROM search_messages, websearch_to_tsquery('english', ?) query
                    WHERE (search_vector @@ query OR content ILIKE ? ESCAPE '\\')
                """
                params: List[Any] = [query, like]
            else:
                sql = """
                    SELECT message_id, content, author_id, author_username,
                           conversation_id, server_id, channel_id,
                           created_at, has_attachments, has_embeds, has_links,
                           is_pinned, attachment_types,
                           ts_rank_cd(search_vector, plainto_tsquery('english', ?)) as rank
                    FROM search_messages
                    WHERE (search_vector @@ plainto_tsquery('english', ?) OR content ILIKE ? ESCAPE '\\')
                """
                params = [query, query, like]

            if conversation_ids:
                sql += f" AND conversation_id IN ({','.join(['?'] * len(conversation_ids))})"
                params.extend(conversation_ids)

            if server_ids:
                sql += f" AND server_id IN ({','.join(['?'] * len(server_ids))})"
                params.extend(server_ids)

            if channel_ids:
                sql += f" AND channel_id IN ({','.join(['?'] * len(channel_ids))})"
                params.extend(channel_ids)

            if author_ids:
                sql += f" AND author_id IN ({','.join(['?'] * len(author_ids))})"
                params.extend(author_ids)

            sql += " ORDER BY rank DESC, created_at DESC LIMIT ? OFFSET ?"
            params.append(max(1, int(limit)))
            params.append(max(0, int(offset)))

            rows = self._db.fetch_all(sql, tuple(params))
            results = []
            for row in rows:
                results.append(
                    MessageSearchResult(
                        id=row["message_id"],
                        message_id=row["message_id"],
                        content=row["content"],
                        author_id=row["author_id"],
                        author_username=row["author_username"] or "",
                        conversation_id=row["conversation_id"],
                        server_id=row["server_id"],
                        channel_id=row["channel_id"],
                        created_at=row["created_at"],
                        has_attachments=bool(row["has_attachments"]),
                        has_embeds=bool(row["has_embeds"]),
                        has_links=bool(row["has_links"]),
                        attachment_types=list((row["attachment_types"] or "").split()),
                        is_pinned=bool(row["is_pinned"]),
                        score=row["rank"],
                    )
                )
            return results
        except Exception as e:
            logger.error(f"Postgres search failed: {e}")
            raise SearchBackendError(
                f"Postgres message search failed: {e}",
                backend="postgres",
                original_error=e,
            )

    def _encode_cursor(self, payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    _EPSILON = 1e-6

    def _decode_cursor(self, cursor: Optional[str]) -> Optional[dict]:
        if not cursor:
            return None
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
            val = json.loads(raw.decode("utf-8"))
            return val if isinstance(val, dict) else None
        except Exception:
            return None

    def _cursor_float_eq(self, a: float, b: float) -> bool:
        return abs(a - b) < self._EPSILON

    def search_messages_page(
        self,
        query: str,
        conversation_ids=None,
        server_ids=None,
        channel_ids=None,
        author_ids=None,
        limit=25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[MessageSearchResult], Optional[str]]:
        try:
            self._ensure_initialized()
            if not query or not query.strip():
                return [], None

            sql = """
                SELECT message_id, content, author_id, conversation_id, server_id, channel_id,
                       created_at, has_attachments, is_pinned,
                       ts_rank_cd(search_vector, query) as rank
                FROM search_messages, websearch_to_tsquery('english', ?) query
                WHERE (search_vector @@ query OR content ILIKE ? ESCAPE '\\')
            """
            params: List[Any] = [query, f"%{_escape_like(query)}%"]

            if conversation_ids:
                sql += f" AND conversation_id IN ({','.join(['?'] * len(conversation_ids))})"
                params.extend(conversation_ids)
            if server_ids:
                sql += f" AND server_id IN ({','.join(['?'] * len(server_ids))})"
                params.extend(server_ids)
            if channel_ids:
                sql += f" AND channel_id IN ({','.join(['?'] * len(channel_ids))})"
                params.extend(channel_ids)
            if author_ids:
                sql += f" AND author_id IN ({','.join(['?'] * len(author_ids))})"
                params.extend(author_ids)

            decoded = self._decode_cursor(cursor)
            if decoded and decoded.get("kind") == "msg":
                sql += """
                    AND (
                        ts_rank_cd(search_vector, query) < ?
                        OR (abs(ts_rank_cd(search_vector, query) - ?) < 0.0001
                            AND created_at < ?)
                        OR (abs(ts_rank_cd(search_vector, query) - ?) < 0.0001
                            AND created_at = ? AND message_id < ?)
                    )
                """
                params.extend(
                    [
                        float(decoded.get("rank", 0.0)),
                        float(decoded.get("rank", 0.0)),
                        int(decoded.get("created_at", 0)),
                        float(decoded.get("rank", 0.0)),
                        int(decoded.get("created_at", 0)),
                        int(decoded.get("id", 0)),
                    ]
                )

            sql += " ORDER BY rank DESC, created_at DESC, message_id DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = self._db.fetch_all(sql, tuple(params))

            results = [
                MessageSearchResult(
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
                    score=row["rank"],
                )
                for row in rows
            ]
            if not rows:
                return results, None
            tail = rows[-1]
            next_cursor = self._encode_cursor(
                {
                    "kind": "msg",
                    "rank": float(tail["rank"]),
                    "created_at": int(tail["created_at"] or 0),
                    "id": int(tail["message_id"]),
                }
            )
            return results, next_cursor
        except Exception as e:
            logger.error(f"Postgres paged message search failed: {e}")
            raise SearchBackendError(
                f"Postgres paged message search failed: {e}",
                backend="postgres",
                original_error=e,
            )

    def update_message(self, message: IndexedMessage) -> bool:
        return self.index_message(message)

    def index_user(self, user: IndexedUser) -> bool:
        try:
            self._ensure_initialized()
            self._db.upsert(
                "search_users",
                ["user_id", "username", "display_name", "is_bot"],
                (user.user_id, user.username, user.display_name, user.is_bot),
                ["user_id"],
            )
            return True
        except Exception as e:
            logger.error(f"Failed to index user {user.user_id} in Postgres: {e}")
            return False

    def remove_user(self, user_id: int) -> bool:
        try:
            self._ensure_initialized()
            self._db.execute("DELETE FROM search_users WHERE user_id = ?", (user_id,))
            return True
        except Exception as e:
            logger.error(f"Failed to remove user {user_id} from Postgres index: {e}")
            return False

    def search_users(self, query: str, limit=25, offset=0):
        try:
            self._ensure_initialized()
            if not query or not query.strip():
                return []

            use_websearch = self._check_websearch_support()
            like = f"%{_escape_like(query)}%"

            if use_websearch:
                sql = """
                    SELECT user_id, username, display_name, is_bot,
                           ts_rank_cd(search_vector, query) as rank
                    FROM search_users, websearch_to_tsquery('english', ?) query
                    WHERE (search_vector @@ query OR username ILIKE ? ESCAPE '\\' OR display_name ILIKE ? ESCAPE '\\')
                    ORDER BY rank DESC LIMIT ? OFFSET ?
                """
                params = [
                    query,
                    like,
                    like,
                    max(1, int(limit)),
                    max(0, int(offset)),
                ]
            else:
                sql = """
                    SELECT user_id, username, display_name, is_bot,
                           ts_rank_cd(search_vector, plainto_tsquery('english', ?)) as rank
                    FROM search_users
                    WHERE (search_vector @@ plainto_tsquery('english', ?) OR username ILIKE ? ESCAPE '\\' OR display_name ILIKE ? ESCAPE '\\')
                    ORDER BY rank DESC LIMIT ? OFFSET ?
                """
                params = [
                    query,
                    query,
                    like,
                    like,
                    max(1, int(limit)),
                    max(0, int(offset)),
                ]

            rows = self._db.fetch_all(sql, tuple(params))
            return [
                UserSearchResult(
                    id=r["user_id"],
                    user_id=r["user_id"],
                    username=r["username"],
                    display_name=r["display_name"],
                    is_bot=bool(r["is_bot"]),
                    score=r["rank"],
                )
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Postgres user search failed: {e}")
            raise SearchBackendError(
                f"Postgres user search failed: {e}",
                backend="postgres",
                original_error=e,
            )

    def search_users_page(
        self,
        query: str,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[UserSearchResult], Optional[str]]:
        try:
            self._ensure_initialized()
            if not query or not query.strip():
                return [], None

            use_websearch = self._check_websearch_support()
            like = f"%{_escape_like(query)}%"

            if use_websearch:
                sql = """
                    SELECT user_id, username, display_name, is_bot,
                           ts_rank_cd(search_vector, query) as rank
                    FROM search_users, websearch_to_tsquery('english', ?) query
                    WHERE (search_vector @@ query OR username ILIKE ? ESCAPE '\\' OR display_name ILIKE ? ESCAPE '\\')
                """
                params: List[Any] = [query, like, like]
            else:
                sql = """
                    SELECT user_id, username, display_name, is_bot,
                           ts_rank_cd(search_vector, plainto_tsquery('english', ?)) as rank
                    FROM search_users
                    WHERE (search_vector @@ plainto_tsquery('english', ?) OR username ILIKE ? ESCAPE '\\' OR display_name ILIKE ? ESCAPE '\\')
                """
                params = [query, query, like, like]

            decoded = self._decode_cursor(cursor)
            if decoded and decoded.get("kind") == "usr":
                sql += """
                    AND (
                        ts_rank_cd(search_vector, query) < ?
                        OR (abs(ts_rank_cd(search_vector, query) - ?) < 0.0001
                            AND user_id < ?)
                    )
                """
                params.extend(
                    [
                        float(decoded.get("rank", 0.0)),
                        float(decoded.get("rank", 0.0)),
                        int(decoded.get("id", 0)),
                    ]
                )

            sql += " ORDER BY rank DESC, user_id DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = self._db.fetch_all(sql, tuple(params))
            results = [
                UserSearchResult(
                    id=r["user_id"],
                    user_id=r["user_id"],
                    username=r["username"],
                    display_name=r["display_name"],
                    is_bot=bool(r["is_bot"]),
                    score=r["rank"],
                )
                for r in rows
            ]
            if not rows:
                return results, None
            tail = rows[-1]
            next_cursor = self._encode_cursor(
                {"kind": "usr", "rank": float(tail["rank"]), "id": int(tail["user_id"])}
            )
            return results, next_cursor
        except Exception as e:
            logger.error(f"Postgres paged user search failed: {e}")
            raise SearchBackendError(
                f"Postgres paged user search failed: {e}",
                backend="postgres",
                original_error=e,
            )

    def index_server(self, server: IndexedServer) -> bool:
        try:
            self._ensure_initialized()
            self._db.upsert(
                "search_servers",
                [
                    "server_id",
                    "name",
                    "description",
                    "tags",
                    "category",
                    "member_count",
                    "is_public",
                ],
                (
                    server.server_id,
                    server.name,
                    server.description,
                    " ".join(server.tags),
                    server.category,
                    server.member_count,
                    server.is_public,
                ),
                ["server_id"],
            )
            return True
        except Exception as e:
            logger.error(f"Failed to index server {server.server_id} in Postgres: {e}")
            return False

    def remove_server(self, server_id: int) -> bool:
        try:
            self._ensure_initialized()
            self._db.execute(
                "DELETE FROM search_servers WHERE server_id = ?", (server_id,)
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to remove server {server_id} from Postgres index: {e}"
            )
            return False

    def search_servers(
        self, query: str, category=None, public_only=True, limit=25, offset=0
    ):
        try:
            self._ensure_initialized()
            if not query or not query.strip():
                return []

            use_websearch = self._check_websearch_support()
            like = f"%{_escape_like(query)}%"

            if use_websearch:
                sql = """
                    SELECT server_id, name, description, category, tags, member_count,
                           ts_rank_cd(search_vector, query) as rank
                    FROM search_servers, websearch_to_tsquery('english', ?) query
                    WHERE (search_vector @@ query OR name ILIKE ? ESCAPE '\\' OR description ILIKE ? ESCAPE '\\')
                """
                params: List[Any] = [query, like, like]
            else:
                sql = """
                    SELECT server_id, name, description, category, tags, member_count,
                           ts_rank_cd(search_vector, plainto_tsquery('english', ?)) as rank
                    FROM search_servers
                    WHERE (search_vector @@ plainto_tsquery('english', ?) OR name ILIKE ? ESCAPE '\\' OR description ILIKE ? ESCAPE '\\')
                """
                params = [query, query, like, like]

            if public_only:
                sql += " AND is_public = TRUE"
            if category:
                sql += " AND category = ?"
                params.append(category)

            sql += " ORDER BY rank DESC LIMIT ? OFFSET ?"
            params.append(max(1, int(limit)))
            params.append(max(0, int(offset)))

            rows = self._db.fetch_all(sql, tuple(params))
            return [
                ServerSearchResult(
                    id=r["server_id"],
                    server_id=r["server_id"],
                    name=r["name"],
                    description=r["description"],
                    category=r["category"],
                    tags=r["tags"].split() if r["tags"] else [],
                    member_count=r["member_count"],
                    score=r["rank"],
                )
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Postgres server search failed: {e}")
            raise SearchBackendError(
                f"Postgres server search failed: {e}",
                backend="postgres",
                original_error=e,
            )

    def search_servers_page(
        self,
        query: str,
        category: Optional[str] = None,
        public_only: bool = True,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[ServerSearchResult], Optional[str]]:
        try:
            self._ensure_initialized()
            if not query or not query.strip():
                return [], None

            use_websearch = self._check_websearch_support()
            like = f"%{_escape_like(query)}%"

            if use_websearch:
                sql = """
                    SELECT server_id, name, description, category, tags, member_count,
                           ts_rank_cd(search_vector, query) as rank
                    FROM search_servers, websearch_to_tsquery('english', ?) query
                    WHERE (search_vector @@ query OR name ILIKE ? ESCAPE '\\' OR description ILIKE ? ESCAPE '\\')
                """
                params: List[Any] = [query, like, like]
            else:
                sql = """
                    SELECT server_id, name, description, category, tags, member_count,
                           ts_rank_cd(search_vector, plainto_tsquery('english', ?)) as rank
                    FROM search_servers
                    WHERE (search_vector @@ plainto_tsquery('english', ?) OR name ILIKE ? ESCAPE '\\' OR description ILIKE ? ESCAPE '\\')
                """
                params = [query, query, like, like]

            if public_only:
                sql += " AND is_public = TRUE"
            if category:
                sql += " AND category = ?"
                params.append(category)

            decoded = self._decode_cursor(cursor)
            if decoded and decoded.get("kind") == "srv":
                sql += """
                    AND (
                        ts_rank_cd(search_vector, query) < ?
                        OR (abs(ts_rank_cd(search_vector, query) - ?) < 0.0001
                            AND server_id < ?)
                    )
                """
                params.extend(
                    [
                        float(decoded.get("rank", 0.0)),
                        float(decoded.get("rank", 0.0)),
                        int(decoded.get("id", 0)),
                    ]
                )

            sql += " ORDER BY rank DESC, server_id DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = self._db.fetch_all(sql, tuple(params))
            results = [
                ServerSearchResult(
                    id=r["server_id"],
                    server_id=r["server_id"],
                    name=r["name"],
                    description=r["description"],
                    category=r["category"],
                    tags=r["tags"].split() if r["tags"] else [],
                    member_count=r["member_count"],
                    score=r["rank"],
                )
                for r in rows
            ]
            if not rows:
                return results, None
            tail = rows[-1]
            next_cursor = self._encode_cursor(
                {
                    "kind": "srv",
                    "rank": float(tail["rank"]),
                    "id": int(tail["server_id"]),
                }
            )
            return results, next_cursor
        except Exception as e:
            logger.error(f"Postgres paged server search failed: {e}")
            raise SearchBackendError(
                f"Postgres paged server search failed: {e}",
                backend="postgres",
                original_error=e,
            )

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
