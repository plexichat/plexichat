"""
Search models - Dataclasses for all search-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class SearchBackend(Enum):
    """Available search backend types."""
    SQLITE_FTS5 = "sqlite_fts5"
    ELASTICSEARCH = "elasticsearch"
    MEILISEARCH = "meilisearch"


class FilterType(Enum):
    """Types of search filters."""
    FROM_USER = "from"
    IN_CHANNEL = "in"
    BEFORE_DATE = "before"
    AFTER_DATE = "after"
    HAS_ATTACHMENT = "has"
    MENTIONS_USER = "mentions"
    PINNED = "pinned"
    EXACT_PHRASE = "exact"


class VerificationLevel(Enum):
    """Server verification levels."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERIFIED = "verified"


@dataclass
class QueryFilter:
    """A single filter in a parsed query."""
    filter_type: FilterType
    value: str
    negated: bool = False


@dataclass
class ParsedQuery:
    """Parsed search query with filters and terms."""
    raw_query: str
    search_terms: List[str] = field(default_factory=list)
    filters: List[QueryFilter] = field(default_factory=list)
    exact_phrases: List[str] = field(default_factory=list)
    
    @property
    def has_filters(self) -> bool:
        """Check if query has any filters."""
        return len(self.filters) > 0
    
    @property
    def search_text(self) -> str:
        """Get combined search terms as text."""
        return " ".join(self.search_terms)


@dataclass
class SearchResult:
    """Base search result."""
    id: int
    score: float = 0.0
    highlights: Dict[str, str] = field(default_factory=dict)


@dataclass
class MessageSearchResult(SearchResult):
    """Search result for a message."""
    message_id: int = 0
    content: str = ""
    author_id: int = 0
    author_username: str = ""
    conversation_id: int = 0
    conversation_name: Optional[str] = None
    server_id: Optional[int] = None
    server_name: Optional[str] = None
    channel_id: Optional[int] = None
    channel_name: Optional[str] = None
    created_at: int = 0
    has_attachments: bool = False
    is_pinned: bool = False


@dataclass
class UserSearchResult(SearchResult):
    """Search result for a user."""
    user_id: int = 0
    username: str = ""
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_bot: bool = False
    mutual_servers: int = 0
    mutual_friends: int = 0


@dataclass
class ServerSearchResult(SearchResult):
    """Search result for a server."""
    server_id: int = 0
    name: str = ""
    description: Optional[str] = None
    icon_url: Optional[str] = None
    member_count: int = 0
    category: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    verification_level: VerificationLevel = VerificationLevel.NONE
    is_verified: bool = False


@dataclass
class ServerCategory:
    """A category for server discovery."""
    id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    server_count: int = 0


@dataclass
class ServerListing:
    """A server listing in the discovery directory."""
    id: int
    server_id: int
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    banner_url: Optional[str] = None
    category: str = ""
    tags: List[str] = field(default_factory=list)
    member_count: int = 0
    online_count: int = 0
    verification_level: VerificationLevel = VerificationLevel.NONE
    is_verified: bool = False
    is_partnered: bool = False
    listed_at: int = 0
    bumped_at: int = 0
    bump_count: int = 0


@dataclass
class IndexedMessage:
    """A message stored in the search index."""
    message_id: int
    content: str
    author_id: int
    conversation_id: int
    server_id: Optional[int] = None
    channel_id: Optional[int] = None
    created_at: int = 0
    has_attachments: bool = False
    has_embeds: bool = False
    has_links: bool = False
    mentions: List[int] = field(default_factory=list)
    is_pinned: bool = False


@dataclass
class IndexedUser:
    """A user stored in the search index."""
    user_id: int
    username: str
    display_name: Optional[str] = None
    discriminator: Optional[str] = None
    is_bot: bool = False


@dataclass
class IndexedServer:
    """A server stored in the search index."""
    server_id: int
    name: str
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    category: Optional[str] = None
    member_count: int = 0
    is_public: bool = False
