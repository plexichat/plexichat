"""
Thread schemas - Request/response models for thread endpoints.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class ThreadCreateRequest(BaseModel):
    """Create-thread request."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=100, description="Thread name")
    thread_type: str = Field(
        "public",
        description="Thread type: public, private, or announcement",
    )
    auto_archive_duration: int = Field(
        1440,
        description="Auto-archive duration in minutes (60, 1440, 4320, 10080)",
    )
    parent_message_id: Optional[SnowflakeID] = Field(
        None, description="Message to create the thread from (reply thread)"
    )


class ThreadUpdateRequest(BaseModel):
    """Update-thread request."""

    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(
        None, min_length=1, max_length=100, description="New thread name"
    )
    auto_archive_duration: Optional[int] = Field(
        None, description="Auto-archive duration in minutes (60, 1440, 4320, 10080)"
    )


class ThreadResponse(BaseModel):
    """Thread response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID
    channel_id: SnowflakeID
    server_id: SnowflakeID
    owner_id: SnowflakeID
    name: str
    thread_type: str
    state: str
    parent_message_id: Optional[SnowflakeID] = None
    auto_archive_duration: int
    message_count: int
    member_count: int
    created_at: int
    archived_at: Optional[int] = None
    last_message_at: Optional[int] = None
    locked: bool = False


class ThreadListResponse(BaseModel):
    """List of threads."""

    model_config = ConfigDict(from_attributes=True)

    threads: List[ThreadResponse]
