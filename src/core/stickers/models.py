"""
Sticker models - Dataclasses for all sticker-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum
from src.core.base import SnowflakeID


class StickerFormat(Enum):
    """Sticker file format."""

    PNG = "png"
    APNG = "apng"
    LOTTIE = "json"


class PackType(Enum):
    """Type of sticker pack.

    NOTE: ``PERSONAL`` is a per-user pack (server_id IS NULL,
    created_by = user_id) that previously had to be expressed as
    ``PackType.SERVER`` with a NULL server_id, which made routing
    queries ambiguous (``WHERE pack_type = SERVER AND server_id IS NULL``
    matched every personal pack AND every mis-typed "server" pack
    with no server). The dedicated variant lets the manager index
    by ``pack_type`` alone.
    """

    DEFAULT = "default"
    SERVER = "server"
    PURCHASED = "purchased"
    PERSONAL = "personal"


@dataclass
class StickerPack:
    """Represents a sticker pack."""

    id: SnowflakeID
    name: str
    description: Optional[str] = None
    pack_type: PackType = PackType.SERVER
    server_id: Optional[SnowflakeID] = None
    created_by: SnowflakeID = 0
    created_at: int = 0
    updated_at: int = 0
    sticker_count: int = 0
    is_public: bool = False


@dataclass
class Sticker:
    """Represents a single sticker."""

    id: SnowflakeID
    pack_id: SnowflakeID
    name: str
    format: StickerFormat = StickerFormat.PNG
    tags: List[str] = field(default_factory=list)
    related_emoji: Optional[str] = None
    url: str = ""
    size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: int = 0
    usage_count: int = 0


@dataclass
class StickerUsage:
    """Tracks sticker usage statistics."""

    id: SnowflakeID
    sticker_id: SnowflakeID
    user_id: SnowflakeID
    message_id: SnowflakeID
    used_at: int


@dataclass
class StickerSuggestion:
    """Sticker suggestion based on message content."""

    sticker: Sticker
    relevance_score: float
    matched_keywords: List[str] = field(default_factory=list)
