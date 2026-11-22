"""
SQLite FTS5 indexer - Full-text search using SQLite FTS5.

This is the default indexer that works out of the box with no external dependencies.
"""

import json
import math
import re
from typing import List, Dict, Any, Optional

import utils.logger as logger

from src.core.database.builder.sql_safety import sql_identifier, sql_placeholders
from src.core.security.hardening import HardeningViolation, reject_control_characters

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

# Column layout the indexer expects for each FTS5 table. A pre-existing
# table with a different layout (e.g. created by an older schema) cannot be
# altered in place by CREATE VIRTUAL TABLE IF NOT EXISTS, so it is detected
# at bootstrap and reported as an actionable, once-per-startup failure.
# The search manager validates these limits at its entry points, but the
# indexer is also callable directly (rebuilds, tests, internal callers). Bound
# the MATCH expression here too so an unbounded string can never reach SQLite:
# a multi-KB needle or thousands of OR'd terms make FTS5 planning expensive
# regardless of the result LIMIT.
_FTS_MAX_QUERY_CHARS = 256
_FTS_MAX_TERMS = 16
_FTS_MAX_TERM_CHARS = 64
_FTS_MAX_RESULTS = 1000
_FTS_MAX_OFFSET = 1_000_000

# Prefix queries are the expensive shape in FTS5: ``"a"*`` forces a scan of
# the whole term dictionary and a merge of every posting list whose term starts
# with that prefix, regardless of the result LIMIT. The length/term caps above
# bound the *size* of the MATCH expression but not that dictionary scan, so a
# query of 1-2 character terms (e.g. ``a b c ... p``) still turns one request
# into a full-index prefix merge. Terms shorter than this are matched exactly
# (no trailing ``*``) instead; real prefix searching is preserved for terms of
# 3+ characters, which select a tiny slice of the dictionary.
_FTS_MIN_PREFIX_TERM_CHARS = 3

# Shared by the single-row and batched write paths. The batched path collapses
# the per-message DELETE + INSERT pair into one executemany of each statement,
# so keeping the SQL text in one place guarantees both paths write identical
# values in identical columns.
_DELETE_MESSAGE_SQL = "DELETE FROM search_messages_fts WHERE message_id = ?"
_INSERT_MESSAGE_SQL = """INSERT INTO search_messages_fts
   (message_id, content, author_id, author_username, conversation_id,
    server_id, channel_id, created_at, has_attachments, has_embeds,
    has_links, mentions, is_pinned, attachment_types)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def _bounded_page(limit, offset):
    """Clamp LIMIT/OFFSET to safe ranges before they reach SQLite.

    A limit of 10**9 turns a single search into a full-index dump; a negative
    limit reads as *unlimited* in SQLite. Both are capped here at the indexer
    boundary even though the manager validates them at its entry points.
    """
    # int(inf) raises OverflowError (not ValueError), and nan would flow into
    # the bound parameter; reject both explicitly before clamping.
    for value, field in ((limit, "limit"), (offset, "offset")):
        if isinstance(value, float) and not math.isfinite(value):
            raise HardeningViolation(f"{field} must be a finite number")
    try:
        limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        limit = 25
    try:
        offset = int(offset)
    except (TypeError, ValueError, OverflowError):
        offset = 0
    return max(1, min(limit, _FTS_MAX_RESULTS)), max(0, min(offset, _FTS_MAX_OFFSET))


_FTS_EXPECTED_COLUMNS = {
    "search_messages_fts": [
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
    "search_users_fts": ["user_id", "username", "display_name", "is_bot"],
    "search_servers_fts": [
        "server_id",
        "name",
        "description",
        "tags",
        "category",
        "member_count",
        "is_public",
    ],
}


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

        # Safety check: Don't run FTS5 initialization on non-SQLite databases
        db_type = getattr(self._db, "type", "sqlite")
        if db_type != "sqlite":
            logger.warning(
                f"SQLite FTS5 indexer cannot initialize on {db_type} database. Skipping."
            )
            return False

        try:
            # Initialization must be idempotent and must not destroy indexed
            # data when a manager is recreated during startup or a worker
            # reload. Schema changes belong in migrations, not in initialize().
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
                    attachment_types UNINDEXED,
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

            # CREATE VIRTUAL TABLE IF NOT EXISTS silently keeps a
            # pre-existing table with an incompatible legacy layout. Probe
            # the live columns once here and fail loud with a rebuild
            # pointer instead of failing every later insert with cryptic
            # mismatch errors.
            mismatched = self._probe_fts_schema()
            if mismatched is not None:
                logger.error(
                    f"FTS5 table '{mismatched}' exists with an incompatible "
                    "legacy schema; the search index must be rebuilt. Run the "
                    "search-index rebuild migration (DROP the stale "
                    "search_*_fts tables and reindex all content) before the "
                    "SQLite FTS5 indexer can start."
                )
                raise SearchBackendError(
                    f"Incompatible legacy FTS5 schema on '{mismatched}'; "
                    "rebuild the search index via the rebuild migration",
                    backend="sqlite_fts5",
                )

            self._initialized = True
            logger.info("SQLite FTS5 indexer initialized")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize FTS5 indexer: {e}")
            raise SearchBackendError(
                f"Failed to initialize FTS5: {e}",
                backend="sqlite_fts5",
                original_error=e,
            )

    def close(self):
        """Close the indexer."""
        self._initialized = False

    @staticmethod
    def _message_insert_params(message: IndexedMessage) -> tuple:
        """Build the FTS5 INSERT tuple for one message.

        Extracted from ``index_message`` so the single-row and batched write
        paths convert fields (None -> "", bools -> "0"/"1", mentions ->
        JSON, attachment types -> space-joined text) identically.
        """
        mentions_json = json.dumps(message.mentions) if message.mentions else "[]"
        return (
            str(message.message_id),
            message.content or "",
            str(message.author_id),
            message.author_username or "",
            str(message.conversation_id),
            str(message.server_id) if message.server_id else "",
            str(message.channel_id) if message.channel_id else "",
            str(message.created_at),
            "1" if message.has_attachments else "0",
            "1" if message.has_embeds else "0",
            "1" if message.has_links else "0",
            mentions_json,
            "1" if message.is_pinned else "0",
            " ".join(message.attachment_types) if message.attachment_types else "",
        )

    def index_message(self, message: IndexedMessage) -> bool:
        """Index a single message."""
        try:
            self._ensure_initialized()

            self._db.execute(
                _DELETE_MESSAGE_SQL,
                (str(message.message_id),),
            )
            self._db.execute(_INSERT_MESSAGE_SQL, self._message_insert_params(message))

            return True

        except Exception as e:
            logger.error(f"Failed to index message {message.message_id}: {e}")
            raise SearchIndexError(
                f"Failed to index message: {e}", item_id=message.message_id
            )

    def _execute_many(self, query: str, params_list) -> None:
        """Run a batch of writes in as few DB round trips as possible.

        The real ``Database`` exposes ``execute_many`` (one driver
        ``executemany`` plus a single commit for the whole batch). Lightweight
        DB-shaped doubles used in tests may only implement ``execute``; keep
        working with those by falling back to a plain loop while production
        gets the batched path.
        """
        execute_many = getattr(self._db, "execute_many", None)
        if callable(execute_many):
            execute_many(query, list(params_list))
        else:
            for params in params_list:
                self._db.execute(query, params)

    def index_messages_batch(self, messages: List[IndexedMessage]) -> int:
        """Index multiple messages in as few DB round trips as possible.

        ``index_message`` issues a DELETE plus an INSERT per message. Reindex
        (the main caller) feeds it thousands of messages, so those two
        statements per row -- each acquiring a pooled connection, being
        monitored and committed on its own -- dominate wall time. Collapse
        them into one batched DELETE and one batched INSERT via ``execute_many``.

        Semantics are preserved:

        * each id ends with exactly one row holding its latest content (a
          duplicate id in the batch is deduplicated keeping the last document,
          matching the old delete-then-insert-per-occurrence behaviour);
        * the return value is the number of input messages, as before;
        * if the backend rejects a batched statement, fall back to the original
          per-row loop so one bad row cannot discard the whole page.
        """
        if not messages:
            return 0

        # Per-row indexing applied last-write-wins for repeated ids. Keep the
        # last occurrence of each id so a batched DELETE + batched INSERT
        # cannot leave two FTS rows for the same message.
        latest_by_id = {}
        for message in messages:
            latest_by_id[str(message.message_id)] = message

        delete_params = [(message_id,) for message_id in latest_by_id]
        insert_params = [
            self._message_insert_params(message) for message in latest_by_id.values()
        ]

        try:
            self._ensure_initialized()
            self._execute_many(_DELETE_MESSAGE_SQL, delete_params)
            self._execute_many(_INSERT_MESSAGE_SQL, insert_params)
            return len(messages)
        except Exception as e:
            logger.warning(
                f"Batched message indexing failed ({e}); "
                "falling back to per-message indexing"
            )
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
                (str(message_id),),
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

            limit, offset = _bounded_page(limit, offset)
            fts_query = self._build_fts_query(query)

            sql = """
                SELECT message_id, content, author_id, author_username,
                       conversation_id, server_id, channel_id, created_at,
                       has_attachments, has_embeds, has_links, is_pinned,
                       attachment_types, rank
                FROM search_messages_fts
                WHERE search_messages_fts MATCH ?
            """
            params: List[Any] = [fts_query]

            if conversation_ids:
                sql += f" AND conversation_id IN ({sql_placeholders(len(conversation_ids))})"
                params.extend(str(cid) for cid in conversation_ids)

            if server_ids:
                sql += f" AND server_id IN ({sql_placeholders(len(server_ids))})"
                params.extend(str(sid) for sid in server_ids)

            if channel_ids:
                sql += f" AND channel_id IN ({sql_placeholders(len(channel_ids))})"
                params.extend(str(chid) for chid in channel_ids)

            if author_ids:
                sql += f" AND author_id IN ({sql_placeholders(len(author_ids))})"
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
                    author_username=row["author_username"] or "",
                    conversation_id=int(row["conversation_id"])
                    if row["conversation_id"]
                    else 0,
                    server_id=int(row["server_id"]) if row["server_id"] else None,
                    channel_id=int(row["channel_id"]) if row["channel_id"] else None,
                    created_at=int(row["created_at"]) if row["created_at"] else 0,
                    has_attachments=row["has_attachments"] == "1",
                    has_embeds=row["has_embeds"] == "1",
                    has_links=row["has_links"] == "1",
                    attachment_types=list((row["attachment_types"] or "").split()),
                    is_pinned=row["is_pinned"] == "1",
                    score=abs(float(row["rank"])) if row["rank"] else 0.0,
                )
                results.append(result)

            return results

        except (SearchBackendError, HardeningViolation):
            raise
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise SearchBackendError(
                f"SQLite FTS5 message search failed: {e}",
                backend="sqlite_fts5",
                original_error=e,
            )

    def index_user(self, user: IndexedUser) -> bool:
        """Index a user."""
        try:
            self._ensure_initialized()

            self._db.execute(
                "DELETE FROM search_users_fts WHERE user_id = ?", (str(user.user_id),)
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
                ),
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
                "DELETE FROM search_users_fts WHERE user_id = ?", (str(user_id),)
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

            limit, offset = _bounded_page(limit, offset)
            fts_query = self._build_fts_query(query)

            rows = self._db.fetch_all(
                """SELECT user_id, username, display_name, is_bot, rank
                   FROM search_users_fts
                   WHERE search_users_fts MATCH ?
                   ORDER BY rank
                   LIMIT ? OFFSET ?""",
                (fts_query, limit, offset),
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

        except (SearchBackendError, HardeningViolation):
            raise
        except Exception as e:
            logger.error(f"User search failed: {e}")
            raise SearchBackendError(
                f"SQLite FTS5 user search failed: {e}",
                backend="sqlite_fts5",
                original_error=e,
            )

    def index_server(self, server: IndexedServer) -> bool:
        """Index a server."""
        try:
            self._ensure_initialized()

            self._db.execute(
                "DELETE FROM search_servers_fts WHERE server_id = ?",
                (str(server.server_id),),
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
                ),
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
                "DELETE FROM search_servers_fts WHERE server_id = ?", (str(server_id),)
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

            limit, offset = _bounded_page(limit, offset)
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
                    categories=[row["category"]] if row["category"] else [],
                    tags=tags,
                    member_count=int(row["member_count"]) if row["member_count"] else 0,
                    score=abs(float(row["rank"])) if row["rank"] else 0.0,
                )
                results.append(result)

            return results

        except (SearchBackendError, HardeningViolation):
            raise
        except Exception as e:
            logger.error(f"Server search failed: {e}")
            raise SearchBackendError(
                f"SQLite FTS5 server search failed: {e}",
                backend="sqlite_fts5",
                original_error=e,
            )

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

    def _probe_fts_schema(self) -> Optional[str]:
        """Return the first FTS5 table whose live columns don't match the
        expected schema, or None when every table lines up."""
        for table, expected in _FTS_EXPECTED_COLUMNS.items():
            # Validate before the try: a HardeningViolation is a programming
            # error, not a probe failure to be swallowed as "schema mismatch".
            safe_table = sql_identifier(table, "table name")
            try:
                rows = self._db.fetch_all(f"PRAGMA table_info({safe_table})")
            except Exception:
                return table
            live = []
            for row in rows or []:
                if isinstance(row, dict):
                    live.append(str(row.get("name", "")))
                elif isinstance(row, (list, tuple)) and row:
                    live.append(str(row[1]))
                else:
                    live.append(str(row))
            if sorted(live) != sorted(expected):
                return table
        return None

    def _build_fts_query(self, query: str) -> str:
        """Build FTS5 query from user query (bounded in length and terms)."""
        # Control characters must never reach the MATCH expression. The
        # manager rejects them at its HTTP entry points, but indexers are also
        # called directly (rebuilds, tests, internal callers): a NUL embedded
        # in even a single quoted phrase survives into the bound MATCH string
        # where sqlite3 raises "unterminated string", turning a search into a
        # 500. Fail closed here too rather than trusting every caller.
        query = reject_control_characters(query, "fts query")

        # Cap before any tokenization: a hostile caller cannot make SQLite
        # parse an arbitrarily large MATCH expression. Truncation keeps normal
        # queries untouched and avoids turning a search into an error.
        query = query.strip()[:_FTS_MAX_QUERY_CHARS]

        special_chars = ["(", ")", "*", ":", "-", "+", "^", "~"]
        clean_query = query
        for char in special_chars:
            clean_query = clean_query.replace(char, " ")

        phrases = []
        for match in re.finditer(r'"([^"]+)"', clean_query):
            phrases.append(match.group(1))

        remaining = re.sub(r'"[^"]*"', "", clean_query)
        terms = [t.strip() for t in remaining.split() if t.strip()]

        all_tokens = []
        # ONE shared budget for quoted phrases and bare terms. Applying
        # ``_FTS_MAX_TERMS`` only to bare terms let a 256-char needle composed
        # of '"a" "b" ...' produce dozens of phrase tokens, defeating the cap
        # and multiplying FTS5 query-planning work on every search.
        budget = _FTS_MAX_TERMS
        for phrase in phrases:
            if budget <= 0:
                break
            safe_phrase = phrase.replace('"', "")[:_FTS_MAX_TERM_CHARS]
            if safe_phrase:
                all_tokens.append(f'"{safe_phrase}"')
                budget -= 1
        for term in terms:
            if budget <= 0:
                break
            safe_term = term.replace('"', "")[:_FTS_MAX_TERM_CHARS]
            if safe_term:
                # Only emit a prefix (*) query for terms long enough to keep
                # the FTS5 term-dictionary scan cheap. Short terms are matched
                # exactly so a burst of 1-2 character tokens cannot force a
                # full-index prefix merge (see _FTS_MIN_PREFIX_TERM_CHARS).
                prefix = "*" if len(safe_term) >= _FTS_MIN_PREFIX_TERM_CHARS else ""
                all_tokens.append(f'"{safe_term}"{prefix}')
                budget -= 1

        if not all_tokens:
            return '""'

        if len(all_tokens) == 1:
            return all_tokens[0]

        return " OR ".join(all_tokens)
