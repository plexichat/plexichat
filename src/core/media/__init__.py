"""
Media module - Zero-friction API for file uploads, processing, and storage.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import media
    media.setup(db, messaging)

    # In any other file (use directly)
    from src.core import media
    result = media.upload_file(user_id=1, file_data=data, filename="image.jpg")
"""

from typing import Optional, List, Dict, Any, BinaryIO, Tuple

from .models import (
    MediaFile,
    MediaType,
    StorageBackend,
    Thumbnail,
    ThumbnailSize,
    ScanStatus,
    UploadResult,
    AttachmentData,
    ImageMetadata,
    VideoMetadata,
    SignedUrl,
    ProxiedContent,
)
from .exceptions import (
    MediaError,
    FileNotFoundError,
    FileUploadError,
    FileSizeError,
    FileTypeError,
    StorageError,
    StorageConnectionError,
    StorageWriteError,
    StorageReadError,
    StorageDeleteError,
    ImageProcessingError,
    VideoProcessingError,
    SigningError,
    SignatureExpiredError,
    SignatureInvalidError,
    ProxyError,
    ProxyFetchError,
    ProxyCacheError,
    ScannerError,
    MalwareDetectedError,
    ScannerUnavailableError,
    PermissionDeniedError,
)

__all__ = [
    # Models
    "MediaFile",
    "MediaType",
    "StorageBackend",
    "Thumbnail",
    "ThumbnailSize",
    "ScanStatus",
    "UploadResult",
    "AttachmentData",
    "ImageMetadata",
    "VideoMetadata",
    "SignedUrl",
    "ProxiedContent",
    # Exceptions
    "MediaError",
    "FileNotFoundError",
    "FileUploadError",
    "FileSizeError",
    "FileTypeError",
    "StorageError",
    "StorageConnectionError",
    "StorageWriteError",
    "StorageReadError",
    "StorageDeleteError",
    "ImageProcessingError",
    "VideoProcessingError",
    "SigningError",
    "SignatureExpiredError",
    "SignatureInvalidError",
    "ProxyError",
    "ProxyFetchError",
    "ProxyCacheError",
    "ScannerError",
    "MalwareDetectedError",
    "ScannerUnavailableError",
    "PermissionDeniedError",
    # Setup
    "setup",
    # File operations
    "upload_file",
    "upload_stream",
    "upload_attachment",
    "get_file",
    "get_file_data",
    "delete_file",
    # URL signing
    "sign_url",
    "verify_signed_url",
    # Thumbnails
    "get_thumbnails",
    "create_thumbnail",
    # Image processing
    "resize_image",
    "convert_image",
    # Video processing
    "get_video_metadata",
    # Proxy
    "proxy_url",
    "get_proxied_content",
    # Scanning
    "scan_file",
]

_manager = None
_setup_complete = False


def setup(db, messaging_module=None):
    """
    Initialize the media module.

    Args:
        db: Database instance (must be connected)
        messaging_module: Optional messaging module for attachment integration
    """
    global _manager, _setup_complete

    from .manager import MediaManager

    _manager = MediaManager(db, messaging_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Media module not initialized. Call media.setup(db) first."
        )
    return _manager


# === File Operations ===


def upload_file(
    user_id: int,
    file_data: bytes,
    filename: str,
    content_type: Optional[str] = None,
) -> UploadResult:
    """
    Upload a file.

    Args:
        user_id: ID of user uploading
        file_data: Raw file bytes
        filename: Original filename
        content_type: MIME type (auto-detected if None)

    Returns:
        UploadResult with file info and URLs
    """
    return _get_manager().upload_file(user_id, file_data, filename, content_type)


def upload_stream(
    user_id: int,
    stream: BinaryIO,
    filename: str,
    content_type: str,
    size: int,
) -> UploadResult:
    """
    Upload a file from stream.

    Args:
        user_id: ID of user uploading
        stream: File-like object
        filename: Original filename
        content_type: MIME type
        size: File size in bytes

    Returns:
        UploadResult with file info and URLs
    """
    return _get_manager().upload_stream(user_id, stream, filename, content_type, size)


def upload_attachment(
    user_id: int,
    file_data: bytes,
    filename: str,
    content_type: Optional[str] = None,
) -> AttachmentData:
    """
    Upload file and return data compatible with messaging attachments.

    Args:
        user_id: ID of user uploading
        file_data: Raw file bytes
        filename: Original filename
        content_type: MIME type

    Returns:
        AttachmentData compatible with messaging module
    """
    return _get_manager().upload_attachment(user_id, file_data, filename, content_type)


def get_file(file_id: int) -> Optional[MediaFile]:
    """Get file by ID."""
    return _get_manager().get_file(file_id)


def get_file_data(file_id: int) -> Tuple[bytes, str]:
    """
    Get file data and content type.

    Args:
        file_id: File ID

    Returns:
        Tuple of (file bytes, content_type)
    """
    return _get_manager().get_file_data(file_id)


def delete_file(user_id: int, file_id: int) -> bool:
    """
    Delete a file (soft delete).

    Args:
        user_id: ID of user deleting
        file_id: File ID

    Returns:
        True if deleted
    """
    return _get_manager().delete_file(user_id, file_id)


# === URL Signing ===


def sign_url(file_id: int, expires_in: Optional[int] = None) -> SignedUrl:
    """
    Generate signed URL for file.

    Args:
        file_id: File ID
        expires_in: Expiration time in seconds

    Returns:
        SignedUrl object
    """
    return _get_manager().sign_url(file_id, expires_in)


def verify_signed_url(url: str) -> Tuple[bool, int]:
    """
    Verify a signed URL.

    Args:
        url: Signed URL

    Returns:
        Tuple of (is_valid, file_id)
    """
    return _get_manager().verify_signed_url(url)


# === Thumbnails ===


def get_thumbnails(file_id: int) -> Dict[int, str]:
    """Get thumbnail URLs for file."""
    return _get_manager().get_thumbnails(file_id)


def create_thumbnail(file_id: int, size: int) -> Optional[str]:
    """
    Create thumbnail at specific size.

    Args:
        file_id: File ID
        size: Thumbnail size

    Returns:
        Thumbnail URL or None
    """
    return _get_manager().create_thumbnail(file_id, size)


# === Image Processing ===


def resize_image(
    file_id: int,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> bytes:
    """
    Resize image file.

    Args:
        file_id: File ID
        width: Target width
        height: Target height

    Returns:
        Resized image bytes
    """
    return _get_manager().resize_image(file_id, width, height)


def convert_image(file_id: int, output_format: str) -> bytes:
    """
    Convert image to different format.

    Args:
        file_id: File ID
        output_format: Target format (JPEG, PNG, WEBP)

    Returns:
        Converted image bytes
    """
    return _get_manager().convert_image(file_id, output_format)


# === Video Processing ===


def get_video_metadata(file_id: int) -> Optional[VideoMetadata]:
    """Get video metadata."""
    return _get_manager().get_video_metadata(file_id)


# === Proxy ===


def proxy_url(url: str, force_refresh: bool = False) -> ProxiedContent:
    """
    Proxy external URL.

    Args:
        url: External URL to proxy
        force_refresh: Force refresh cache

    Returns:
        ProxiedContent object
    """
    return _get_manager().proxy_url(url, force_refresh)


def get_proxied_content(url: str) -> Tuple[bytes, str]:
    """
    Get proxied content.

    Args:
        url: External URL

    Returns:
        Tuple of (content bytes, content_type)
    """
    return _get_manager().get_proxied_content(url)


# === Scanning ===


def scan_file(file_id: int) -> Tuple[ScanStatus, Optional[str]]:
    """
    Scan file for malware.

    Args:
        file_id: File ID

    Returns:
        Tuple of (ScanStatus, threat_name or None)
    """
    return _get_manager().scan_file(file_id)
