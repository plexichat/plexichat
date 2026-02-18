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

from typing import Optional, Dict, Any, BinaryIO, Tuple

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
    "get_file_by_filename",
    "get_file_data",
    "get_file_stream",
    "delete_file",
    "check_file_access",
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
    # Deduplication
    "check_duplicate",
    "report_hash",
    "is_hash_blocked",
    # Compression
    "compress_file",
    "get_compression_status",
    # Chunked uploads
    "create_upload_session",
    "upload_chunk",
    "complete_upload_session",
    "cancel_upload_session",
]

_manager = None
_dedup_manager = None
_compression_manager = None
_chunked_manager = None
_setup_complete = False


def setup(db: Any, messaging_module: Optional[Any] = None) -> None:
    """
    Initialize the media module.

    Args:
        db: Database instance (must be connected)
        messaging_module: Optional messaging module for attachment integration
    """
    global \
        _manager, \
        _dedup_manager, \
        _compression_manager, \
        _chunked_manager, \
        _setup_complete

    from .manager import MediaManager
    from .deduplication import DeduplicationManager
    from .compression import CompressionManager
    from .chunked import ChunkedUploadManager

    _manager = MediaManager(db, messaging_module)
    _dedup_manager = DeduplicationManager(db)
    _compression_manager = CompressionManager()
    _chunked_manager = ChunkedUploadManager(db)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError("Media module not initialized. Call media.setup(db) first.")
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


def get_file_by_filename(filename: str) -> Optional[MediaFile]:
    """Get file by filename."""
    return _get_manager().get_file_by_filename(filename)


def get_file_data(file_id: int) -> Tuple[bytes, str]:
    """
    Get file data and content type.

    Args:
        file_id: File ID

    Returns:
        Tuple of (file bytes, content_type)
    """
    return _get_manager().get_file_data(file_id)


def get_file_stream(file_id: int) -> Tuple[BinaryIO, int, str]:
    """
    Get file data as a stream.

    Args:
        file_id: File ID

    Returns:
        Tuple of (file stream, size, content_type)
    """
    return _get_manager().get_file_stream(file_id)


def get_file_stream_optimized(
    path: str, content_type: str, backend: str
) -> Tuple[BinaryIO, int, str]:
    """
    Get file data as a stream directly (avoids DB lookup).

    Args:
        path: Storage path
        content_type: Content type
        backend: Storage backend value

    Returns:
        Tuple of (file stream, size, content_type)
    """
    return _get_manager().get_file_stream_optimized(path, content_type, backend)


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


def check_file_access(filename: str, user_id: int) -> bool:
    """Check if a user can access a media file."""
    return _get_manager().check_file_access(filename, user_id)


# === URL Signing ===


def sign_url(
    file_id: int, expires_in: Optional[int] = None, params: Optional[dict] = None
) -> SignedUrl:
    """
    Generate signed URL for file.

    Args:
        file_id: File ID
        expires_in: Expiration time in seconds
        params: Optional storage-specific parameters

    Returns:
        SignedUrl object
    """
    return _get_manager().sign_url(file_id, expires_in, params)


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


def create_thumbnail(
    file_id: int, size: int, user_id: Optional[int] = None
) -> Optional[str]:
    """
    Create thumbnail at specific size.

    Args:
        file_id: File ID
        size: Thumbnail size
        user_id: Optional user ID for rate limiting

    Returns:
        Thumbnail URL or None
    """
    return _get_manager().create_thumbnail(file_id, size, user_id)


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


# === Deduplication ===


def _get_dedup_manager():
    """Get the deduplication manager instance."""
    if not _setup_complete or _dedup_manager is None:
        raise RuntimeError("Media module not initialized. Call media.setup(db) first.")
    return _dedup_manager


def check_duplicate(file_data: bytes, content_type: str):
    """
    Check if file is a duplicate or blocked.

    Args:
        file_data: Raw file bytes
        content_type: MIME type

    Returns:
        DeduplicationResult with duplicate/block status
    """
    return _get_dedup_manager().check_duplicate(file_data, content_type)


def report_hash(
    hash_value: str,
    reporter_id: int,
    reason: str,
    details: Optional[str] = None,
    phash_value: Optional[str] = None,
    uploader_id: Optional[int] = None,
    message_id: Optional[int] = None,
    attachment_url: Optional[str] = None,
    block_uploader: bool = False,
) -> int:
    """
    Report a file hash for content moderation.

    Args:
        hash_value: SHA-256 hash of the file
        reporter_id: User ID of reporter
        reason: Reason for report
        details: Additional details
        phash_value: Perceptual hash (for images)
        uploader_id: User ID who uploaded the content
        message_id: Message ID containing the attachment
        attachment_url: URL of the attachment
        block_uploader: Whether to request blocking the uploader

    Returns:
        Report ID
    """
    return _get_dedup_manager().report_hash(
        hash_value=hash_value,
        reporter_id=reporter_id,
        reason=reason,
        details=details,
        phash_value=phash_value,
        uploader_id=uploader_id,
        message_id=message_id,
        attachment_url=attachment_url,
        block_uploader=block_uploader,
    )


def is_hash_blocked(hash_value: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a hash is blocked.

    Args:
        hash_value: SHA-256 hash

    Returns:
        Tuple of (is_blocked, reason)
    """
    return _get_dedup_manager().is_blocked(hash_value)


# === Compression ===


def _get_compression_manager():
    """Get the compression manager instance."""
    if not _setup_complete or _compression_manager is None:
        raise RuntimeError("Media module not initialized. Call media.setup(db) first.")
    return _compression_manager


def compress_file(file_data: bytes, content_type: str, quality: Optional[str] = None):
    """
    Compress a file.

    Args:
        file_data: Raw file bytes
        content_type: MIME type
        quality: Quality preset ('low', 'medium', 'high', 'original')

    Returns:
        CompressionResult with compressed data
    """
    from .compression import CompressionQuality

    q = CompressionQuality(quality) if quality else None
    return _get_compression_manager().compress(file_data, content_type, q)


def get_compression_status() -> Dict[str, Any]:
    """Get compression system status."""
    return _get_compression_manager().get_status()


# === Chunked Uploads ===


def _get_chunked_manager():
    """Get the chunked upload manager instance."""
    if not _setup_complete or _chunked_manager is None:
        raise RuntimeError("Media module not initialized. Call media.setup(db) first.")
    return _chunked_manager


def create_upload_session(
    user_id: int, filename: str, content_type: str, total_size: int
):
    """
    Create a chunked upload session.

    Args:
        user_id: User ID
        filename: Original filename
        content_type: MIME type
        total_size: Total file size in bytes

    Returns:
        UploadSession or None
    """
    return _get_chunked_manager().create_session(
        user_id, filename, content_type, total_size
    )


def upload_chunk(
    session_id: str,
    user_id: int,
    chunk_index: int,
    chunk_data: bytes,
    chunk_checksum: Optional[str] = None,
):
    """
    Upload a chunk to a session.

    Args:
        session_id: Session ID
        user_id: User ID
        chunk_index: Zero-based chunk index
        chunk_data: Chunk bytes
        chunk_checksum: Optional MD5 checksum

    Returns:
        ChunkUploadResult
    """
    return _get_chunked_manager().upload_chunk(
        session_id, user_id, chunk_index, chunk_data, chunk_checksum
    )


def complete_upload_session(session_id: str, user_id: int) -> Optional[bytes]:
    """
    Complete an upload session and return the assembled file.

    Args:
        session_id: Session ID
        user_id: User ID

    Returns:
        Complete file bytes or None
    """
    return _get_chunked_manager().complete_session(session_id, user_id)


def cancel_upload_session(session_id: str, user_id: int) -> bool:
    """Cancel an upload session."""
    return _get_chunked_manager().cancel_session(session_id, user_id)
