"""
Base indexer - Abstract interface for search indexers.
"""

from abc import ABC, abstractmethod
import base64
import json
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from ..models import (
    IndexedMessage,
    IndexedUser,
    IndexedServer,
    MessageSearchResult,
    UserSearchResult,
    ServerSearchResult,
)


@dataclass
class IndexerConfig:
    """Configuration for search indexer."""

    batch_size: int = 100
    write_time_indexing: bool = True
    result_limit: int = 100
    highlight_pre_tag: str = "<mark>"
    highlight_post_tag: str = "</mark>"
    extra: Dict[str, Any] = field(default_factory=dict)


class BaseIndexer(ABC):
    """Abstract base class for search indexers."""

    def __init__(self, config: Optional[IndexerConfig] = None):
        self.config = config or IndexerConfig()

    @abstractmethod
    def initialize(self) -> bool:
        """
        Initialize the indexer and create necessary structures.

        Returns:
            True if initialization successful
        """
        raise NotImplementedError()

    @abstractmethod
    def close(self):
        """Close the indexer and release resources."""
        raise NotImplementedError()

    @abstractmethod
    def index_message(self, message: IndexedMessage) -> bool:
        """
        Index a single message.

        Args:
            message: Message to index

        Returns:
            True if indexed successfully
        """
        raise NotImplementedError()

    @abstractmethod
    def index_messages_batch(self, messages: List[IndexedMessage]) -> int:
        """
        Index multiple messages in batch.

        Args:
            messages: List of messages to index

        Returns:
            Number of messages indexed
        """
        raise NotImplementedError()

    @abstractmethod
    def remove_message(self, message_id: int) -> bool:
        """
        Remove a message from the index.

        Args:
            message_id: ID of message to remove

        Returns:
            True if removed successfully
        """
        raise NotImplementedError()

    @abstractmethod
    def update_message(self, message: IndexedMessage) -> bool:
        """
        Update an indexed message.

        Args:
            message: Updated message data

        Returns:
            True if updated successfully
        """
        raise NotImplementedError()

    @abstractmethod
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
        """
        Search messages.

        Args:
            query: Search query
            conversation_ids: Filter by conversations
            server_ids: Filter by servers
            channel_ids: Filter by channels
            author_ids: Filter by authors
            limit: Maximum results
            offset: Offset for pagination

        Returns:
            List of search results
        """
        pass

    def search_messages_page(
        self,
        query: str,
        conversation_ids: Optional[List[int]] = None,
        server_ids: Optional[List[int]] = None,
        channel_ids: Optional[List[int]] = None,
        author_ids: Optional[List[int]] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[MessageSearchResult], Optional[str]]:
        """
        Search messages with cursor pagination.

        Args:
            query: Search query
            conversation_ids: Filter by conversations
            server_ids: Filter by servers
            channel_ids: Filter by channels
            author_ids: Filter by authors
            limit: Maximum results
            cursor: Cursor for pagination

        Returns:
            Tuple of (results, next_cursor)
        """
        decoded = self._decode_offset_cursor(cursor, "msg")
        offset = decoded.get("offset", 0) if decoded else 0
        page_limit = max(1, int(limit))
        results = self.search_messages(
            query=query,
            conversation_ids=conversation_ids,
            server_ids=server_ids,
            channel_ids=channel_ids,
            author_ids=author_ids,
            limit=page_limit,
            offset=offset,
        )
        next_cursor = None
        if len(results) == page_limit:
            next_cursor = self._encode_offset_cursor("msg", offset + len(results))
        return results, next_cursor

    @abstractmethod
    def index_user(self, user: IndexedUser) -> bool:
        """
        Index a user.

        Args:
            user: User to index

        Returns:
            True if indexed successfully
        """
        pass

    @abstractmethod
    def remove_user(self, user_id: int) -> bool:
        """
        Remove a user from the index.

        Args:
            user_id: ID of user to remove

        Returns:
            True if removed successfully
        """
        pass

    @abstractmethod
    def search_users(
        self,
        query: str,
        limit: int = 25,
        offset: int = 0,
    ) -> List[UserSearchResult]:
        """
        Search users.

        Args:
            query: Search query
            limit: Maximum results
            offset: Offset for pagination

        Returns:
            List of user search results
        """
        pass

    def search_users_page(
        self,
        query: str,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[UserSearchResult], Optional[str]]:
        """
        Search users with cursor pagination.

        Args:
            query: Search query
            limit: Maximum results
            cursor: Cursor for pagination

        Returns:
            Tuple of (results, next_cursor)
        """
        decoded = self._decode_offset_cursor(cursor, "usr")
        offset = decoded.get("offset", 0) if decoded else 0
        page_limit = max(1, int(limit))
        results = self.search_users(query=query, limit=page_limit, offset=offset)
        next_cursor = None
        if len(results) == page_limit:
            next_cursor = self._encode_offset_cursor("usr", offset + len(results))
        return results, next_cursor

    @abstractmethod
    def index_server(self, server: IndexedServer) -> bool:
        """
        Index a server.

        Args:
            server: Server to index

        Returns:
            True if indexed successfully
        """
        pass

    @abstractmethod
    def remove_server(self, server_id: int) -> bool:
        """
        Remove a server from the index.

        Args:
            server_id: ID of server to remove

        Returns:
            True if removed successfully
        """
        pass

    @abstractmethod
    def search_servers(
        self,
        query: str,
        category: Optional[str] = None,
        public_only: bool = True,
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerSearchResult]:
        """
        Search servers.

        Args:
            query: Search query
            category: Filter by category
            public_only: Only search public servers
            limit: Maximum results
            offset: Offset for pagination

        Returns:
            List of server search results
        """
        pass

    def search_servers_page(
        self,
        query: str,
        category: Optional[str] = None,
        public_only: bool = True,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[ServerSearchResult], Optional[str]]:
        """
        Search servers with cursor pagination.

        Args:
            query: Search query
            category: Filter by category
            public_only: Only search public servers
            limit: Maximum results
            cursor: Cursor for pagination

        Returns:
            Tuple of (results, next_cursor)
        """
        decoded = self._decode_offset_cursor(cursor, "srv")
        offset = decoded.get("offset", 0) if decoded else 0
        page_limit = max(1, int(limit))
        results = self.search_servers(
            query=query,
            category=category,
            public_only=public_only,
            limit=page_limit,
            offset=offset,
        )
        next_cursor = None
        if len(results) == page_limit:
            next_cursor = self._encode_offset_cursor("srv", offset + len(results))
        return results, next_cursor

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """
        Get indexer statistics.

        Returns:
            Dict with stats (message_count, user_count, server_count, etc.)
        """
        pass

    def health_check(self) -> bool:
        """
        Check if indexer is healthy.

        Returns:
            True if healthy
        """
        try:
            self.get_stats()
            return True
        except Exception:
            return False

    def _encode_offset_cursor(self, kind: str, offset: int) -> str:
        payload = {"kind": kind, "offset": max(0, int(offset))}
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def _decode_offset_cursor(
        self,
        cursor: Optional[str],
        expected_kind: str,
    ) -> Optional[Dict[str, int]]:
        if not cursor:
            return None
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("kind") != expected_kind:
            return None
        try:
            offset = max(0, int(payload.get("offset", 0)))
        except (TypeError, ValueError):
            return None
        return {"offset": offset}
