"""
Thread models - Dataclasses for all thread-related entities.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from src.core.base import SnowflakeID


class ThreadType(Enum):
    """Types of threads."""

    PUBLIC = "public"
    PRIVATE = "private"
    ANNOUNCEMENT = "announcement"


class ThreadState(Enum):
    """Thread states."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    LOCKED = "locked"


class AutoArchiveDuration(Enum):
    """Auto-archive duration options in minutes."""

    ONE_HOUR = 60
    ONE_DAY = 1440
    THREE_DAYS = 4320
    SEVEN_DAYS = 10080


@dataclass
class Thread:
    """Thread entity."""

    id: SnowflakeID
    channel_id: SnowflakeID
    server_id: SnowflakeID
    owner_id: SnowflakeID
    name: str
    thread_type: ThreadType
    state: ThreadState
    parent_message_id: Optional[SnowflakeID]
    auto_archive_duration: AutoArchiveDuration
    message_count: int
    member_count: int
    created_at: int
    archived_at: Optional[int]
    last_message_at: Optional[int]
    conversation_id: Optional[SnowflakeID] = None
    locked: bool = False


@dataclass
class ThreadMember:
    """Thread member entity."""

    thread_id: SnowflakeID
    user_id: SnowflakeID
    joined_at: int
    last_read_message_id: Optional[SnowflakeID] = None
    muted: bool = False
