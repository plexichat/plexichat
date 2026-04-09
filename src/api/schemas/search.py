"""
Search schemas - Request/response models for search endpoints.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from .common import SnowflakeID
from .messages import MessageResponse


class UserSearchResultResponse(BaseModel):
    """User search result equivalent to UserSearchResult."""

    model_config = ConfigDict(from_attributes=True)

    user_id: SnowflakeID
    username: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    badges: List[str] = Field(default_factory=list)
    score: float = 0.0


class ServerSearchResultResponse(BaseModel):
    """Server search result equivalent to ServerSearchResult."""

    model_config = ConfigDict(from_attributes=True)

    server_id: SnowflakeID
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    member_count: int = 0
    category: Optional[str] = None
    score: float = 0.0


class MessageSearchPageResponse(BaseModel):
    """Paginated message search results."""

    model_config = ConfigDict(from_attributes=True)

    results: List[MessageResponse]
    next_cursor: Optional[str] = None


class UserSearchPageResponse(BaseModel):
    """Paginated user search results."""

    model_config = ConfigDict(from_attributes=True)

    results: List[UserSearchResultResponse]
    next_cursor: Optional[str] = None


class ServerSearchPageResponse(BaseModel):
    """Paginated server search results."""

    model_config = ConfigDict(from_attributes=True)

    results: List[ServerSearchResultResponse]
    next_cursor: Optional[str] = None
