"""
Thread models - Dataclasses for all thread-related entities.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
    id: int
    channel_id: int
    server_id: int
    owner_id: int
    name: str
    thread_type: ThreadType
    state: ThreadState
    parent_message_id: Optional[int]
    auto_archive_duration: AutoArchiveDuration
    message_count: int
    member_count: int
    created_at: int
    archived_at: Optional[int]
    last_message_at: Optional[int]
    locked: bool = False


@dataclass
class ThreadMember:
    """Thread member entity."""
    thread_id: int
    user_id: int
    joined_at: int
    last_read_message_id: Optional[int] = None
    muted: bool = False
