"""
Embed models - Dataclasses for all embed-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class EmbedType(Enum):
    """Type of embed."""
    RICH = "rich"
    IMAGE = "image"
    VIDEO = "video"
    GIFV = "gifv"
    ARTICLE = "article"
    LINK = "link"


@dataclass
class EmbedFooter:
    """Embed footer section."""
    text: str
    icon_url: Optional[str] = None
    proxy_icon_url: Optional[str] = None


@dataclass
class EmbedImage:
    """Embed image section."""
    url: str
    proxy_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class EmbedThumbnail:
    """Embed thumbnail section."""
    url: str
    proxy_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class EmbedAuthor:
    """Embed author section."""
    name: str
    url: Optional[str] = None
    icon_url: Optional[str] = None
    proxy_icon_url: Optional[str] = None


@dataclass
class EmbedProvider:
    """Embed provider section (for URL previews)."""
    name: Optional[str] = None
    url: Optional[str] = None


@dataclass
class EmbedField:
    """Embed field (name/value pair)."""
    name: str
    value: str
    inline: bool = False


@dataclass
class Embed:
    """Represents a rich embed."""
    id: int
    embed_type: EmbedType = EmbedType.RICH
    title: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[str] = None
    color: Optional[str] = None
    footer: Optional[EmbedFooter] = None
    image: Optional[EmbedImage] = None
    thumbnail: Optional[EmbedThumbnail] = None
    author: Optional[EmbedAuthor] = None
    provider: Optional[EmbedProvider] = None
    fields: List[EmbedField] = field(default_factory=list)
    created_by: int = 0
    created_at: int = 0
    is_url_preview: bool = False
    source_url: Optional[str] = None


@dataclass
class MessageEmbed:
    """Association between a message and an embed."""
    id: int
    message_id: int
    embed_id: int
    position: int = 0
    suppressed: bool = False
    created_at: int = 0
