"""
Search manager - Core business logic for search operations.

Handles message search, user search, server search, and discovery
with proper permission checks and multiple backend support.
"""

from typing import List, Dict, Any, Optional

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager

from .models import (
    ParsedQuery,
    MessageSearchResult,
    UserSearchResult,
    ServerSearchResult,
    ServerListing,
    ServerCategory,
    VerificationLevel,
    IndexedMessage,
    IndexedUser,
    IndexedServer,
)
from .exceptions import (
    SearchLimitError,
)
from .schema import create_tables
from .query import QueryParser, FilterProcessor, RankingEngine
from .indexer import SQLiteFTS5Indexer, ElasticsearchIndexer, MeilisearchIndexer
from .indexer.base import IndexerConfig
from .discovery import DiscoveryManager


class SearchManager(BaseManager):
    """Core search manager handling all search operations."""

    def __init__(self, db, auth_module=None, messaging_module=None, servers_module=None):
        """
        Initialize the search manager.
        
        Args:
            db: Database instance (must be connected)
            auth_module: Auth module for user data
            messaging_module: Messaging module for message access
            servers_module: Servers module for server data and permissions
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._config = self._load_config()

        create_tables(db)

        self._indexer = self._create_indexer()
        self._indexer.initialize()

        self._query_parser = QueryParser()
        self._filter_processor = FilterProcessor(db, auth_module, servers_module)
        self._ranking_engine = RankingEngine()

        self._discovery = DiscoveryManager(db, servers_module)

        logger.info("Search module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load search configuration."""
        defaults = {
            "backend": "sqlite_fts5",
            "batch_size": 100,
            "write_time_indexing": True,
            "result_limit": 100,
            "elasticsearch": {
                "hosts": ["http://localhost:9200"],
                "index_prefix": "plexichat",
            },
            "meilisearch": {
                "host": "http://localhost:7700",
                "api_key": None,
                "index_prefix": "plexichat",
            },
            "discovery": {
                "min_members_for_listing": 10,
                "bump_cooldown_hours": 4,
                "max_tags": 10,
            },
        }

        search_config = config.get("search", {})
        return {**defaults, **search_config}

    def _create_indexer(self):
        """Create the appropriate indexer based on configuration."""
        backend = self._config.get("backend", "sqlite_fts5")

        indexer_config = IndexerConfig(
            batch_size=self._config.get("batch_size", 100),
            write_time_indexing=self._config.get("write_time_indexing", True),
            result_limit=self._config.get("result_limit", 100),
        )

        if backend == "elasticsearch":
            es_config = self._config.get("elasticsearch", {})
            return ElasticsearchIndexer(
                hosts=es_config.get("hosts", ["http://localhost:9200"]),
                index_prefix=es_config.get("index_prefix", "plexichat"),
                config=indexer_config,
            )

        if backend == "meilisearch":
            ms_config = self._config.get("meilisearch", {})
            return MeilisearchIndexer(
                host=ms_config.get("host", "http://localhost:7700"),
                api_key=ms_config.get("api_key"),
                index_prefix=ms_config.get("index_prefix", "plexichat"),
                config=indexer_config,
            )

        return SQLiteFTS5Indexer(self._db, indexer_config)

    # === Message Search ===

    def search_messages(
        self,
        user_id: int,
        query: str,
        conversation_id: Optional[int] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[MessageSearchResult]:
        """
        Search messages with advanced query syntax.
        
        Args:
            user_id: ID of user performing search
            query: Search query string
            conversation_id: Optional conversation to search within
            server_id: Optional server to search within
            channel_id: Optional channel to search within
            limit: Maximum results to return
            offset: Offset for pagination
            
        Returns:
            List of MessageSearchResult objects
        """
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit
            )

        parsed = self._query_parser.parse(query)

        accessible_conversations = self._get_accessible_conversations(
            user_id, conversation_id, server_id, channel_id
        )

        if not accessible_conversations:
            return []

        search_text = parsed.search_text if parsed.search_terms else query

        results = self._indexer.search_messages(
            query=search_text,
            conversation_ids=accessible_conversations,
            server_ids=[server_id] if server_id else None,
            channel_ids=[channel_id] if channel_id else None,
            limit=limit * 2,
            offset=offset,
        )

        results = self._filter_processor.apply_filters(results, parsed, user_id)

        results = self._enrich_message_results(results, user_id)

        results = self._ranking_engine.rank_message_results(
            results, parsed, self._get_timestamp()
        )

        return results[:limit]

    def index_message(
        self,
        message_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Index a message for search.
        
        Args:
            message_id: ID of message to index
            content: Message content
            metadata: Additional metadata
        """
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
        self._db.upsert(
            "search_message_index",
            ["message_id", "conversation_id", "server_id", "channel_id", "author_id", "indexed_at", "updated_at"],
            (message_id, indexed.conversation_id, indexed.server_id, indexed.channel_id, indexed.author_id, now, now),
            ["message_id"],
            ["conversation_id", "server_id", "channel_id", "author_id", "updated_at"]
        )

    def remove_from_index(self, message_id: int):
        """Remove a message from the search index."""
        self._indexer.remove_message(message_id)
        self._db.execute(
            "DELETE FROM search_message_index WHERE message_id = ?",
            (message_id,)
        )

    def reindex_conversation(self, conversation_id: int):
        """Reindex all messages in a conversation."""
        if not self._messaging:
            return

        messages = self._db.fetch_all(
            """SELECT id, content, author_id, created_at 
               FROM msg_messages 
               WHERE conversation_id = ? AND deleted = 0""",
            (conversation_id,)
        )

        for msg in messages:
            self.index_message(
                message_id=msg["id"],
                content=msg["content"] or "",
                metadata={
                    "author_id": msg["author_id"],
                    "conversation_id": conversation_id,
                    "created_at": msg["created_at"],
                }
            )

    # === User Search ===

    def search_users(
        self,
        user_id: int,
        query: str,
        server_id: Optional[int] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[UserSearchResult]:
        """
        Search users by username or display name.
        
        Args:
            user_id: ID of user performing search
            query: Search query string
            server_id: Optional server to search within
            limit: Maximum results to return
            offset: Offset for pagination
            
        Returns:
            List of UserSearchResult objects
        """
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit
            )

        results = self._indexer.search_users(query, limit * 2, offset)

        if server_id:
            server_members = self._get_server_member_ids(server_id)
            results = [r for r in results if r.user_id in server_members]

        results = self._enrich_user_results(results, user_id)

        results = self._ranking_engine.rank_user_results(results, query, user_id)

        return results[:limit]

    def index_user(self, user_id: int, username: str, display_name: Optional[str] = None, is_bot: bool = False):
        """Index a user for search."""
        indexed = IndexedUser(
            user_id=user_id,
            username=username,
            display_name=display_name,
            is_bot=is_bot,
        )

        self._indexer.index_user(indexed)

        now = self._get_timestamp()
        self._db.upsert(
            "search_user_index",
            ["user_id", "indexed_at", "updated_at"],
            (user_id, now, now),
            ["user_id"],
            ["updated_at"]
        )

    # === Server Search ===

    def search_servers(
        self,
        user_id: int,
        query: str,
        category: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerSearchResult]:
        """
        Search servers by name, description, or tags.
        
        Args:
            user_id: ID of user performing search
            query: Search query string
            category: Optional category filter
            limit: Maximum results to return
            offset: Offset for pagination
            
        Returns:
            List of ServerSearchResult objects
        """
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit
            )

        results = self._indexer.search_servers(
            query=query,
            category=category,
            public_only=True,
            limit=limit * 2,
            offset=offset,
        )

        results = self._enrich_server_results(results)

        results = self._ranking_engine.rank_server_results(results, query)

        return results[:limit]

    def index_server(
        self,
        server_id: int,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        category: Optional[str] = None,
        member_count: int = 0,
        is_public: bool = False,
    ):
        """Index a server for search."""
        indexed = IndexedServer(
            server_id=server_id,
            name=name,
            description=description,
            tags=tags or [],
            category=category,
            member_count=member_count,
            is_public=is_public,
        )

        self._indexer.index_server(indexed)

        now = self._get_timestamp()
        self._db.upsert(
            "search_server_index",
            ["server_id", "indexed_at", "updated_at"],
            (server_id, now, now),
            ["server_id"],
            ["updated_at"]
        )

    # === Discovery ===

    def list_public_servers(
        self,
        category: Optional[str] = None,
        sort_by: str = "member_count",
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerListing]:
        """List public servers in the discovery directory."""
        return self._discovery.list_public_servers(category, sort_by, limit, offset)

    def get_server_categories(self) -> List[ServerCategory]:
        """Get all available server categories."""
        return self._discovery.get_server_categories()

    def list_server(
        self,
        user_id: int,
        server_id: int,
        category: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ServerListing:
        """List a server in the public directory."""
        return self._discovery.list_server(user_id, server_id, category, description, tags)

    def unlist_server(self, user_id: int, server_id: int) -> bool:
        """Remove a server from the public directory."""
        return self._discovery.unlist_server(user_id, server_id)

    def verify_server(self, server_id: int, level: VerificationLevel) -> bool:
        """Set verification level for a server."""
        return self._discovery.verify_server(server_id, level)

    def bump_server(self, user_id: int, server_id: int) -> bool:
        """Bump a server in the discovery listing."""
        return self._discovery.bump_server(user_id, server_id)

    # === Query Utilities ===

    def parse_query(self, query: str) -> ParsedQuery:
        """Parse a search query into structured components."""
        return self._query_parser.parse(query)

    def get_search_suggestions(
        self,
        user_id: int,
        partial_query: str,
        limit: int = 10,
    ) -> List[str]:
        """Get search suggestions based on partial query."""
        suggestions = []

        filter_suggestions = self._query_parser.get_filter_suggestions(partial_query)
        suggestions.extend(filter_suggestions)

        if len(suggestions) < limit:
            history = self._db.fetch_all(
                """SELECT DISTINCT query FROM search_history 
                   WHERE user_id = ? AND query LIKE ?
                   ORDER BY searched_at DESC LIMIT ?""",
                (user_id, f"{partial_query}%", limit - len(suggestions))
            )
            suggestions.extend(row["query"] for row in history)

        return suggestions[:limit]

    # === Helper Methods ===

    def _get_accessible_conversations(
        self,
        user_id: int,
        conversation_id: Optional[int] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
    ) -> List[int]:
        """Get list of conversation IDs the user can access."""
        if conversation_id:
            if self._can_access_conversation(user_id, conversation_id):
                return [conversation_id]
            return []

        if channel_id:
            channel = self._db.fetch_one(
                "SELECT conversation_id FROM srv_channels WHERE id = ?",
                (channel_id,)
            )
            if channel and self._can_access_channel(user_id, channel_id):
                return [channel["conversation_id"]]
            return []

        conversations = []

        dm_convs = self._db.fetch_all(
            """SELECT conversation_id FROM msg_participants 
               WHERE user_id = ?""",
            (user_id,)
        )
        conversations.extend(row["conversation_id"] for row in dm_convs)

        if server_id:
            channels = self._db.fetch_all(
                """SELECT id, conversation_id FROM srv_channels 
                   WHERE server_id = ?""",
                (server_id,)
            )
            for ch in channels:
                if ch["conversation_id"] and self._can_access_channel(user_id, ch["id"]):
                    conversations.append(ch["conversation_id"])
        else:
            user_servers = self._db.fetch_all(
                "SELECT server_id FROM srv_members WHERE user_id = ?",
                (user_id,)
            )
            for srv in user_servers:
                channels = self._db.fetch_all(
                    "SELECT id, conversation_id FROM srv_channels WHERE server_id = ?",
                    (srv["server_id"],)
                )
                for ch in channels:
                    if ch["conversation_id"]:
                        conversations.append(ch["conversation_id"])

        return list(set(conversations))

    def _can_access_conversation(self, user_id: int, conversation_id: int) -> bool:
        """Check if user can access a conversation."""
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id)
        )
        return row is not None

    def _can_access_channel(self, user_id: int, channel_id: int) -> bool:
        """Check if user can access a channel."""
        if not self._servers:
            return True

        channel = self._db.fetch_one(
            "SELECT server_id FROM srv_channels WHERE id = ?",
            (channel_id,)
        )
        if not channel:
            return False

        member = self._db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (channel["server_id"], user_id)
        )
        return member is not None

    def _get_server_member_ids(self, server_id: int) -> set:
        """Get set of user IDs who are members of a server."""
        rows = self._db.fetch_all(
            "SELECT user_id FROM srv_members WHERE server_id = ?",
            (server_id,)
        )
        return {row["user_id"] for row in rows}

    def _enrich_message_results(
        self,
        results: List[MessageSearchResult],
        user_id: int,
    ) -> List[MessageSearchResult]:
        """Enrich message results with additional data."""
        for result in results:
            author = self._db.fetch_one(
                "SELECT username FROM auth_users WHERE id = ?",
                (result.author_id,)
            )
            if author:
                result.author_username = author["username"]

            conv = self._db.fetch_one(
                "SELECT name, conversation_type FROM msg_conversations WHERE id = ?",
                (result.conversation_id,)
            )
            if conv:
                result.conversation_name = conv["name"]

            if result.server_id:
                server = self._db.fetch_one(
                    "SELECT name FROM srv_servers WHERE id = ?",
                    (result.server_id,)
                )
                if server:
                    result.server_name = server["name"]

            if result.channel_id:
                channel = self._db.fetch_one(
                    "SELECT name FROM srv_channels WHERE id = ?",
                    (result.channel_id,)
                )
                if channel:
                    result.channel_name = channel["name"]

        return results

    def _enrich_user_results(
        self,
        results: List[UserSearchResult],
        user_id: int,
    ) -> List[UserSearchResult]:
        """Enrich user results with mutual servers/friends."""
        user_servers = self._get_user_server_ids(user_id)

        for result in results:
            target_servers = self._get_user_server_ids(result.user_id)
            result.mutual_servers = len(user_servers & target_servers)

        return results

    def _enrich_server_results(
        self,
        results: List[ServerSearchResult],
    ) -> List[ServerSearchResult]:
        """Enrich server results with additional data."""
        for result in results:
            listing = self._discovery.get_listing(result.server_id)
            if listing:
                result.verification_level = listing.verification_level
                result.is_verified = listing.is_verified

        return results

    def _get_user_server_ids(self, user_id: int) -> set:
        """Get set of server IDs the user is a member of."""
        rows = self._db.fetch_all(
            "SELECT server_id FROM srv_members WHERE user_id = ?",
            (user_id,)
        )
        return {row["server_id"] for row in rows}
