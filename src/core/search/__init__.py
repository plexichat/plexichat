"""
Search module - Zero-friction API for full-text search and server discovery.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import search
    search.setup(db, auth, messaging, servers)

    # In any other file (use directly)
    from src.core import search
    results = search.search_messages(user_id=1, query="from:alice hello")
"""

from typing import Optional, List, Dict, Any

from .models import (
    SearchResult,
    MessageSearchResult,
    UserSearchResult,
    ServerSearchResult,
    ParsedQuery,
    QueryFilter,
    FilterType,
    SearchBackend,
    ServerCategory,
    ServerListing,
    VerificationLevel,
)
from .exceptions import (
    SearchError,
    SearchNotFoundError,
    SearchPermissionError,
    SearchQueryError,
    SearchIndexError,
    SearchBackendError,
    InvalidQuerySyntaxError,
    SearchLimitError,
    DiscoveryError,
    ServerNotListedError,
    VerificationError,
)

__all__ = [
    # Models
    "SearchResult",
    "MessageSearchResult",
    "UserSearchResult",
    "ServerSearchResult",
    "ParsedQuery",
    "QueryFilter",
    "FilterType",
    "SearchBackend",
    "ServerCategory",
    "ServerListing",
    "VerificationLevel",
    # Exceptions
    "SearchError",
    "SearchNotFoundError",
    "SearchPermissionError",
    "SearchQueryError",
    "SearchIndexError",
    "SearchBackendError",
    "InvalidQuerySyntaxError",
    "SearchLimitError",
    "DiscoveryError",
    "ServerNotListedError",
    "VerificationError",
    # Setup
    "setup",
    # Message search
    "search_messages",
    "index_message",
    "remove_from_index",
    "reindex_conversation",
    # User search
    "search_users",
    # Server search
    "search_servers",
    # Discovery
    "list_public_servers",
    "get_server_categories",
    "list_server",
    "unlist_server",
    "verify_server",
    "bump_server",
    # Query utilities
    "parse_query",
    "get_search_suggestions",
]

_manager = None
_setup_complete = False


def setup(db, auth_module=None, messaging_module=None, servers_module=None):
    """
    Initialize the search module.

    Args:
        db: Database instance (must be connected)
        auth_module: Auth module for user data
        messaging_module: Messaging module for message access
        servers_module: Servers module for server data and permissions
    """
    global _manager, _setup_complete

    from .manager import SearchManager

    _manager = SearchManager(db, auth_module, messaging_module, servers_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Search module not initialized. Call search.setup(db) first."
        )
    return _manager


# === Message Search ===


def search_messages(
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
    
    Supports filters: from:user, in:channel, before:date, after:date,
    has:link/image/file/embed, mentions:user, pinned:true, "exact phrase"
    
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
    return _get_manager().search_messages(
        user_id=user_id,
        query=query,
        conversation_id=conversation_id,
        server_id=server_id,
        channel_id=channel_id,
        limit=limit,
        offset=offset,
    )


def index_message(message_id: int, content: str, metadata: Dict[str, Any] = None):
    """
    Index a message for search.
    
    Args:
        message_id: ID of message to index
        content: Message content
        metadata: Additional metadata (author_id, conversation_id, etc.)
    """
    return _get_manager().index_message(message_id, content, metadata)


def remove_from_index(message_id: int):
    """
    Remove a message from the search index.
    
    Args:
        message_id: ID of message to remove
    """
    return _get_manager().remove_from_index(message_id)


def reindex_conversation(conversation_id: int):
    """
    Reindex all messages in a conversation.
    
    Args:
        conversation_id: ID of conversation to reindex
    """
    return _get_manager().reindex_conversation(conversation_id)


# === User Search ===


def search_users(
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
    return _get_manager().search_users(
        user_id=user_id,
        query=query,
        server_id=server_id,
        limit=limit,
        offset=offset,
    )


# === Server Search ===


def search_servers(
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
    return _get_manager().search_servers(
        user_id=user_id,
        query=query,
        category=category,
        limit=limit,
        offset=offset,
    )


# === Discovery ===


def list_public_servers(
    category: Optional[str] = None,
    sort_by: str = "member_count",
    limit: int = 25,
    offset: int = 0,
) -> List[ServerListing]:
    """
    List public servers in the discovery directory.
    
    Args:
        category: Optional category filter
        sort_by: Sort order (member_count, bumped_at, created_at)
        limit: Maximum results to return
        offset: Offset for pagination
        
    Returns:
        List of ServerListing objects
    """
    return _get_manager().list_public_servers(
        category=category,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )


def get_server_categories() -> List[ServerCategory]:
    """
    Get all available server categories.
    
    Returns:
        List of ServerCategory objects
    """
    return _get_manager().get_server_categories()


def list_server(
    user_id: int,
    server_id: int,
    category: str,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> ServerListing:
    """
    List a server in the public directory.
    
    Args:
        user_id: ID of user listing server (must be owner/admin)
        server_id: ID of server to list
        category: Category for the listing
        description: Optional description override
        tags: Optional tags for discovery
        
    Returns:
        ServerListing object
    """
    return _get_manager().list_server(
        user_id=user_id,
        server_id=server_id,
        category=category,
        description=description,
        tags=tags,
    )


def unlist_server(user_id: int, server_id: int) -> bool:
    """
    Remove a server from the public directory.
    
    Args:
        user_id: ID of user unlisting server (must be owner/admin)
        server_id: ID of server to unlist
        
    Returns:
        True if unlisted
    """
    return _get_manager().unlist_server(user_id, server_id)


def verify_server(server_id: int, level: VerificationLevel) -> bool:
    """
    Set verification level for a server (admin only).
    
    Args:
        server_id: ID of server to verify
        level: Verification level to set
        
    Returns:
        True if verified
    """
    return _get_manager().verify_server(server_id, level)


def bump_server(user_id: int, server_id: int) -> bool:
    """
    Bump a server in the discovery listing.
    
    Args:
        user_id: ID of user bumping server
        server_id: ID of server to bump
        
    Returns:
        True if bumped
    """
    return _get_manager().bump_server(user_id, server_id)


# === Query Utilities ===


def parse_query(query: str) -> ParsedQuery:
    """
    Parse a search query into structured components.
    
    Args:
        query: Raw query string
        
    Returns:
        ParsedQuery object with filters and search terms
    """
    return _get_manager().parse_query(query)


def get_search_suggestions(
    user_id: int,
    partial_query: str,
    limit: int = 10,
) -> List[str]:
    """
    Get search suggestions based on partial query.
    
    Args:
        user_id: ID of user requesting suggestions
        partial_query: Partial query string
        limit: Maximum suggestions to return
        
    Returns:
        List of suggested query completions
    """
    return _get_manager().get_search_suggestions(user_id, partial_query, limit)
