"""
Sticker models - Dataclasses for all sticker-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class StickerFormat(Enum):
    """Sticker file format."""
    PNG = "png"
    APNG = "apng"
    LOTTIE = "json"


class PackType(Enum):
    """Type of sticker pack."""
    DEFAULT = "default"
    SERVER = "server"
    PURCHASED = "purchased"


@dataclass
class StickerPack:
    """Represents a sticker pack."""
    id: int
    name: str
    description: Optional[str] = None
    pack_type: PackType = PackType.SERVER
    server_id: Optional[int] = None
    created_by: int = 0
    created_at: int = 0
    updated_at: int = 0
    sticker_count: int = 0
    is_public: bool = False


@dataclass
class Sticker:
    """Represents a single sticker."""
    id: int
    pack_id: int
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
    id: int
    sticker_id: int
    user_id: int
    message_id: int
    used_at: int


@dataclass
class StickerSuggestion:
    """Sticker suggestion based on message content."""
    sticker: Sticker
    relevance_score: float
    matched_keywords: List[str] = field(default_factory=list)
