"""
Media models - Dataclasses for all media-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum


class MediaType(Enum):
    """Type of media file."""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class StorageBackend(Enum):
    """Storage backend type."""
    LOCAL = "local"
    S3 = "s3"
    DATABASE = "database"


class ThumbnailSize(Enum):
    """Standard thumbnail sizes."""
    TINY = 64
    SMALL = 128
    MEDIUM = 256
    LARGE = 512


class ScanStatus(Enum):
    """Malware scan status."""
    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class MediaFile:
    """Represents an uploaded media file."""
    id: int
    filename: str
    original_filename: str
    content_type: str
    size: int
    media_type: MediaType
    storage_backend: StorageBackend
    storage_path: str
    url: Optional[str] = None
    checksum: Optional[str] = None
    uploaded_by: int = 0
    uploaded_at: int = 0
    metadata: Optional[Dict[str, Any]] = None
    scan_status: ScanStatus = ScanStatus.PENDING
    scan_result: Optional[str] = None
    deleted: bool = False
    deleted_at: Optional[int] = None


@dataclass
class Thumbnail:
    """Represents a generated thumbnail."""
    id: int
    media_file_id: int
    size: int
    width: int
    height: int
    storage_path: str
    url: Optional[str] = None
    created_at: int = 0


@dataclass
class ImageMetadata:
    """Metadata for image files."""
    width: int
    height: int
    format: str
    mode: Optional[str] = None
    has_alpha: bool = False
    animated: bool = False
    frame_count: int = 1
    exif: Optional[Dict[str, Any]] = None


@dataclass
class VideoMetadata:
    """Metadata for video files."""
    width: int
    height: int
    duration: float
    codec: Optional[str] = None
    bitrate: Optional[int] = None
    fps: Optional[float] = None
    audio_codec: Optional[str] = None
    audio_bitrate: Optional[int] = None
    audio_channels: Optional[int] = None
    audio_sample_rate: Optional[int] = None


@dataclass
class SignedUrl:
    """Represents a signed URL with expiration."""
    url: str
    expires_at: int
    signature: str
    file_id: int


@dataclass
class ProxiedContent:
    """Represents cached proxied content."""
    id: int
    source_url: str
    content_type: str
    size: int
    storage_path: str
    cached_at: int
    expires_at: int
    last_accessed: int
    access_count: int = 0
    checksum: Optional[str] = None


@dataclass
class UploadResult:
    """Result of a file upload operation."""
    file_id: int
    filename: str
    content_type: str
    size: int
    url: str
    thumbnails: Dict[int, str] = field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class AttachmentData:
    """Data format compatible with messaging module attachments."""
    filename: str
    content_type: str
    size: int
    url: str
    metadata: Optional[Dict[str, Any]] = None
