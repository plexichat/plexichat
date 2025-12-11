"""
Base indexer - Abstract interface for search indexers.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
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
        pass

    @abstractmethod
    def close(self):
        """Close the indexer and release resources."""
        pass

    @abstractmethod
    def index_message(self, message: IndexedMessage) -> bool:
        """
        Index a single message.
        
        Args:
            message: Message to index
            
        Returns:
            True if indexed successfully
        """
        pass

    @abstractmethod
    def index_messages_batch(self, messages: List[IndexedMessage]) -> int:
        """
        Index multiple messages in batch.
        
        Args:
            messages: List of messages to index
            
        Returns:
            Number of messages indexed
        """
        pass

    @abstractmethod
    def remove_message(self, message_id: int) -> bool:
        """
        Remove a message from the index.
        
        Args:
            message_id: ID of message to remove
            
        Returns:
            True if removed successfully
        """
        pass

    @abstractmethod
    def update_message(self, message: IndexedMessage) -> bool:
        """
        Update an indexed message.
        
        Args:
            message: Updated message data
            
        Returns:
            True if updated successfully
        """
        pass

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
