"""
Media manager - Core business logic for media operations.

Handles file uploads, processing, storage, and URL signing.
"""

import os
import time
import hashlib
import mimetypes
import uuid
from typing import Optional, List, Dict, Any, BinaryIO, Tuple

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

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
    FileUploadError,
    FileSizeError,
    FileTypeError,
    StorageError,
    ImageProcessingError,
    VideoProcessingError,
    PermissionDeniedError,
)
from .schema import create_tables
from .storage import LocalStorage, S3Storage, DatabaseStorage, StorageBackendBase
from .processing import ImageProcessor, VideoProcessor
from .security import UrlSigner, MalwareScanner, ExternalProxy


DEFAULT_SIZE_LIMITS = {
    "image": 10 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
    "audio": 50 * 1024 * 1024,
    "document": 25 * 1024 * 1024,
    "other": 10 * 1024 * 1024,
}

DEFAULT_ALLOWED_TYPES = {
    "image": ["image/jpeg", "image/png", "image/gif", "image/webp"],
    "video": ["video/mp4", "video/webm", "video/quicktime"],
    "audio": ["audio/mpeg", "audio/ogg", "audio/wav", "audio/webm"],
    "document": ["application/pdf", "text/plain", "application/zip"],
}

DEFAULT_THUMBNAIL_SIZES = [64, 128, 256, 512]


class MediaManager:
    """Core media manager handling all operations."""

    def __init__(self, db, messaging_module=None):
        """
        Initialize the media manager.

        Args:
            db: Database instance (must be connected)
            messaging_module: Optional messaging module for attachment integration
        """
        self._db = db
        self._messaging = messaging_module
        self._config = self._load_config()
        
        self._storage = self._init_storage()
        self._image_processor = self._init_image_processor()
        self._video_processor = self._init_video_processor()
        self._url_signer = self._init_url_signer()
        self._scanner = self._init_scanner()
        self._proxy = self._init_proxy()
        
        create_tables(db)
        
        logger.info("Media module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load media configuration."""
        defaults = {
            "storage_backend": "local",
            "local_path": "uploads",
            "local_url": "/media",
            "s3_bucket": "",
            "s3_access_key": "",
            "s3_secret_key": "",
            "s3_region": "us-east-1",
            "s3_endpoint": "",
            "s3_public_url": "",
            "database_url": "/api/v1/media/blob",
            "database_max_size": 512 * 1024,  # 512KB default for DB storage
            "size_limits": DEFAULT_SIZE_LIMITS.copy(),
            "allowed_types": DEFAULT_ALLOWED_TYPES.copy(),
            "thumbnail_sizes": DEFAULT_THUMBNAIL_SIZES.copy(),
            "signing_key": "change-this-secret-key",
            "signing_expiry": 3600,
            "scanner_enabled": False,
            "scanner_host": "localhost",
            "scanner_port": 3310,
            "proxy_enabled": True,
            "proxy_cache_ttl": 86400,
            "proxy_max_size": 10 * 1024 * 1024,
        }
        
        media_config = config.get("media", {})
        
        merged = defaults.copy()
        for key, value in media_config.items():
            if key in merged:
                if isinstance(merged[key], dict) and isinstance(value, dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
        
        return merged

    def _init_storage(self) -> StorageBackendBase:
        """Initialize storage backend."""
        backend = self._config.get("storage_backend", "local")
        
        if backend == "s3":
            return S3Storage(
                bucket=self._config["s3_bucket"],
                access_key=self._config["s3_access_key"],
                secret_key=self._config["s3_secret_key"],
                region=self._config.get("s3_region", "us-east-1"),
                endpoint_url=self._config.get("s3_endpoint") or None,
                public_url=self._config.get("s3_public_url") or None,
            )
        elif backend == "database":
            return DatabaseStorage(
                db=self._db,
                base_url=self._config.get("database_url", "/api/v1/media/blob"),
                max_size=self._config.get("database_max_size", 512 * 1024),
            )
        else:
            return LocalStorage(
                base_path=self._config.get("local_path", "uploads"),
                base_url=self._config.get("local_url", "/media"),
            )

    def _init_image_processor(self) -> Optional[ImageProcessor]:
        """Initialize image processor."""
        try:
            return ImageProcessor(
                quality=self._config.get("image_quality", 85),
                optimize=self._config.get("image_optimize", True),
            )
        except ImageProcessingError:
            logger.warning("Image processing unavailable - Pillow not installed")
            return None

    def _init_video_processor(self) -> VideoProcessor:
        """Initialize video processor."""
        processor = VideoProcessor(
            ffprobe_path=self._config.get("ffprobe_path"),
        )
        if not processor.is_available():
            logger.warning("Video processing unavailable - ffprobe not found")
        return processor

    def _init_url_signer(self) -> UrlSigner:
        """Initialize URL signer."""
        return UrlSigner(
            secret_key=self._config.get("signing_key", "change-this-secret-key"),
            default_expiry=self._config.get("signing_expiry", 3600),
        )

    def _init_scanner(self) -> MalwareScanner:
        """Initialize malware scanner."""
        return MalwareScanner(
            host=self._config.get("scanner_host", "localhost"),
            port=self._config.get("scanner_port", 3310),
            enabled=self._config.get("scanner_enabled", False),
        )

    def _init_proxy(self) -> Optional[ExternalProxy]:
        """Initialize external proxy."""
        if not self._config.get("proxy_enabled", True):
            return None
        
        try:
            return ExternalProxy(
                storage_backend=self._storage,
                db=self._db,
                cache_ttl=self._config.get("proxy_cache_ttl", 86400),
                max_size=self._config.get("proxy_max_size", 10 * 1024 * 1024),
            )
        except Exception as e:
            logger.warning(f"External proxy unavailable: {e}")
            return None

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def _detect_media_type(self, content_type: str) -> MediaType:
        """Detect media type from content type."""
        ct = content_type.lower()
        if ct.startswith("image/"):
            return MediaType.IMAGE
        elif ct.startswith("video/"):
            return MediaType.VIDEO
        elif ct.startswith("audio/"):
            return MediaType.AUDIO
        elif ct in ("application/pdf", "text/plain", "application/msword"):
            return MediaType.DOCUMENT
        return MediaType.OTHER

    def _validate_content_type(self, content_type: str, media_type: MediaType):
        """Validate content type is allowed."""
        allowed = self._config.get("allowed_types", DEFAULT_ALLOWED_TYPES)
        type_key = media_type.value
        
        if type_key in allowed:
            if content_type.lower() not in allowed[type_key]:
                raise FileTypeError(
                    f"Content type not allowed: {content_type}",
                    content_type,
                    allowed[type_key],
                )

    def _validate_file_size(self, size: int, media_type: MediaType):
        """Validate file size is within limits."""
        limits = self._config.get("size_limits", DEFAULT_SIZE_LIMITS)
        type_key = media_type.value
        max_size = limits.get(type_key, limits.get("other", 10 * 1024 * 1024))
        
        if size > max_size:
            raise FileSizeError(
                f"File size {size} exceeds limit {max_size}",
                max_size,
                size,
            )

    def _generate_storage_path(self, filename: str, media_type: MediaType) -> str:
        """Generate storage path for file."""
        ext = os.path.splitext(filename)[1].lower() or ".bin"
        unique_id = uuid.uuid4().hex[:16]
        timestamp = int(time.time())
        
        type_dir = media_type.value
        date_dir = time.strftime("%Y/%m/%d")
        
        return f"{type_dir}/{date_dir}/{unique_id}{ext}"

    def _compute_checksum(self, data: bytes) -> str:
        """Compute SHA-256 checksum."""
        return hashlib.sha256(data).hexdigest()

    def upload_file(
        self,
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
        if not content_type:
            content_type, _ = mimetypes.guess_type(filename)
            content_type = content_type or "application/octet-stream"
        
        media_type = self._detect_media_type(content_type)
        
        self._validate_content_type(content_type, media_type)
        self._validate_file_size(len(file_data), media_type)
        
        scan_status = ScanStatus.SKIPPED
        scan_result = None
        
        if self._scanner and self._scanner.is_available():
            try:
                scan_status, scan_result = self._scanner.scan_bytes(file_data)
                if scan_status == ScanStatus.INFECTED:
                    raise FileUploadError(f"Malware detected: {scan_result}", filename)
            except Exception as e:
                logger.warning(f"Scan failed: {e}")
                scan_status = ScanStatus.ERROR
                scan_result = str(e)
        
        storage_path = self._generate_storage_path(filename, media_type)
        checksum = self._compute_checksum(file_data)
        
        self._storage.store(file_data, storage_path, content_type)
        
        file_id = self._generate_id()
        now = self._get_timestamp()
        
        metadata = {}
        if media_type == MediaType.IMAGE and self._image_processor:
            try:
                img_meta = self._image_processor.get_metadata(file_data)
                metadata = {
                    "width": img_meta.width,
                    "height": img_meta.height,
                    "format": img_meta.format,
                    "has_alpha": img_meta.has_alpha,
                    "animated": img_meta.animated,
                }
            except Exception as e:
                logger.warning(f"Failed to extract image metadata: {e}")
        elif media_type == MediaType.VIDEO and self._video_processor and self._video_processor.is_available():
            try:
                vid_meta = self._video_processor.get_metadata_from_bytes(file_data)
                metadata = {
                    "width": vid_meta.width,
                    "height": vid_meta.height,
                    "duration": vid_meta.duration,
                    "codec": vid_meta.codec,
                }
            except Exception as e:
                logger.warning(f"Failed to extract video metadata: {e}")
        
        import json
        metadata_json = json.dumps(metadata) if metadata else None
        
        self._db.execute(
            """INSERT INTO media_files
               (id, filename, original_filename, content_type, size, media_type,
                storage_backend, storage_path, checksum, uploaded_by, uploaded_at,
                metadata, scan_status, scan_result, deleted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                file_id,
                os.path.basename(storage_path),
                filename,
                content_type,
                len(file_data),
                media_type.value,
                self._config.get("storage_backend", "local"),
                storage_path,
                checksum,
                user_id,
                now,
                metadata_json,
                scan_status.value,
                scan_result,
            )
        )
        
        thumbnails = {}
        if media_type == MediaType.IMAGE and self._image_processor:
            thumbnails = self._generate_thumbnails(file_id, file_data)
        
        url = self._storage.get_url(storage_path)
        
        logger.debug(f"File {file_id} uploaded by user {user_id}: {filename}")
        
        return UploadResult(
            file_id=file_id,
            filename=filename,
            content_type=content_type,
            size=len(file_data),
            url=url,
            thumbnails=thumbnails,
            metadata=metadata,
        )

    def upload_stream(
        self,
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
        media_type = self._detect_media_type(content_type)
        
        self._validate_content_type(content_type, media_type)
        self._validate_file_size(size, media_type)
        
        storage_path = self._generate_storage_path(filename, media_type)
        
        self._storage.store_stream(stream, storage_path, content_type, size)
        
        file_id = self._generate_id()
        now = self._get_timestamp()
        
        self._db.execute(
            """INSERT INTO media_files
               (id, filename, original_filename, content_type, size, media_type,
                storage_backend, storage_path, checksum, uploaded_by, uploaded_at,
                metadata, scan_status, scan_result, deleted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                file_id,
                os.path.basename(storage_path),
                filename,
                content_type,
                size,
                media_type.value,
                self._config.get("storage_backend", "local"),
                storage_path,
                None,
                user_id,
                now,
                None,
                ScanStatus.PENDING.value,
                None,
            )
        )
        
        url = self._storage.get_url(storage_path)
        
        logger.debug(f"File {file_id} uploaded via stream by user {user_id}: {filename}")
        
        return UploadResult(
            file_id=file_id,
            filename=filename,
            content_type=content_type,
            size=size,
            url=url,
            thumbnails={},
            metadata=None,
        )

    def _generate_thumbnails(self, file_id: int, image_data: bytes) -> Dict[int, str]:
        """Generate thumbnails for image."""
        if not self._image_processor:
            return {}
        
        sizes = self._config.get("thumbnail_sizes", DEFAULT_THUMBNAIL_SIZES)
        thumbnails = {}
        
        try:
            results = self._image_processor.create_thumbnails(image_data, sizes)
            
            for size, (thumb_data, width, height) in results.items():
                thumb_path = f"thumbnails/{file_id}/{size}.jpg"
                self._storage.store(thumb_data, thumb_path, "image/jpeg")
                
                thumb_id = self._generate_id()
                now = self._get_timestamp()
                
                self._db.execute(
                    """INSERT INTO media_thumbnails
                       (id, media_file_id, size, width, height, storage_path, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (thumb_id, file_id, size, width, height, thumb_path, now)
                )
                
                thumbnails[size] = self._storage.get_url(thumb_path)
        except Exception as e:
            logger.warning(f"Failed to generate thumbnails: {e}")
        
        return thumbnails

    def get_file(self, file_id: int) -> Optional[MediaFile]:
        """Get file by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE id = ? AND deleted = 0",
            (file_id,)
        )
        
        if not row:
            return None
        
        return self._row_to_media_file(row)

    def get_file_data(self, file_id: int) -> Tuple[bytes, str]:
        """
        Get file data and content type.

        Args:
            file_id: File ID

        Returns:
            Tuple of (file bytes, content_type)
        """
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        
        data = self._storage.retrieve(file.storage_path)
        return data, file.content_type

    def delete_file(self, user_id: int, file_id: int) -> bool:
        """
        Delete a file (soft delete).

        Args:
            user_id: ID of user deleting
            file_id: File ID

        Returns:
            True if deleted
        """
        file = self.get_file(file_id)
        if not file:
            return False
        
        if file.uploaded_by != user_id:
            raise PermissionDeniedError("Can only delete own files")
        
        now = self._get_timestamp()
        
        self._db.execute(
            "UPDATE media_files SET deleted = 1, deleted_at = ? WHERE id = ?",
            (now, file_id)
        )
        
        logger.debug(f"File {file_id} deleted by user {user_id}")
        
        return True

    def upload_attachment(
        self,
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
        result = self.upload_file(user_id, file_data, filename, content_type)
        
        metadata = result.metadata or {}
        if result.thumbnails:
            metadata["thumbnails"] = result.thumbnails
        metadata["file_id"] = result.file_id
        
        return AttachmentData(
            filename=result.filename,
            content_type=result.content_type,
            size=result.size,
            url=result.url,
            metadata=metadata if metadata else None,
        )

    def sign_url(
        self,
        file_id: int,
        expires_in: Optional[int] = None,
    ) -> SignedUrl:
        """
        Generate signed URL for file.

        Args:
            file_id: File ID
            expires_in: Expiration time in seconds

        Returns:
            SignedUrl object
        """
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        
        url = self._storage.get_url(file.storage_path)
        return self._url_signer.sign_url(url, file_id, expires_in)

    def verify_signed_url(self, url: str) -> Tuple[bool, int]:
        """
        Verify a signed URL.

        Args:
            url: Signed URL

        Returns:
            Tuple of (is_valid, file_id)
        """
        return self._url_signer.verify_url(url)

    def get_thumbnails(self, file_id: int) -> Dict[int, str]:
        """Get thumbnail URLs for file."""
        rows = self._db.fetch_all(
            "SELECT * FROM media_thumbnails WHERE media_file_id = ?",
            (file_id,)
        )
        
        return {
            row["size"]: self._storage.get_url(row["storage_path"])
            for row in rows
        }

    def create_thumbnail(
        self,
        file_id: int,
        size: int,
    ) -> Optional[str]:
        """
        Create thumbnail at specific size.

        Args:
            file_id: File ID
            size: Thumbnail size

        Returns:
            Thumbnail URL or None
        """
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.IMAGE:
            return None
        
        if not self._image_processor:
            return None
        
        existing = self._db.fetch_one(
            "SELECT * FROM media_thumbnails WHERE media_file_id = ? AND size = ?",
            (file_id, size)
        )
        
        if existing:
            return self._storage.get_url(existing["storage_path"])
        
        try:
            image_data = self._storage.retrieve(file.storage_path)
            thumb_data, width, height = self._image_processor.create_thumbnail(image_data, size)
            
            thumb_path = f"thumbnails/{file_id}/{size}.jpg"
            self._storage.store(thumb_data, thumb_path, "image/jpeg")
            
            thumb_id = self._generate_id()
            now = self._get_timestamp()
            
            self._db.execute(
                """INSERT INTO media_thumbnails
                   (id, media_file_id, size, width, height, storage_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (thumb_id, file_id, size, width, height, thumb_path, now)
            )
            
            return self._storage.get_url(thumb_path)
        except Exception as e:
            logger.warning(f"Failed to create thumbnail: {e}")
            return None

    def resize_image(
        self,
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
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.IMAGE:
            raise MediaError("File not found or not an image")
        
        if not self._image_processor:
            raise ImageProcessingError("Image processing not available", "resize")
        
        image_data = self._storage.retrieve(file.storage_path)
        resized, _, _ = self._image_processor.resize(image_data, width, height)
        return resized

    def convert_image(
        self,
        file_id: int,
        output_format: str,
    ) -> bytes:
        """
        Convert image to different format.

        Args:
            file_id: File ID
            output_format: Target format (JPEG, PNG, WEBP)

        Returns:
            Converted image bytes
        """
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.IMAGE:
            raise MediaError("File not found or not an image")
        
        if not self._image_processor:
            raise ImageProcessingError("Image processing not available", "convert")
        
        image_data = self._storage.retrieve(file.storage_path)
        return self._image_processor.convert_format(image_data, output_format)

    def get_video_metadata(self, file_id: int) -> Optional[VideoMetadata]:
        """Get video metadata."""
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.VIDEO:
            return None
        
        if not self._video_processor or not self._video_processor.is_available():
            return None
        
        try:
            video_data = self._storage.retrieve(file.storage_path)
            return self._video_processor.get_metadata_from_bytes(video_data)
        except Exception as e:
            logger.warning(f"Failed to get video metadata: {e}")
            return None

    def proxy_url(self, url: str, force_refresh: bool = False) -> ProxiedContent:
        """
        Proxy external URL.

        Args:
            url: External URL to proxy
            force_refresh: Force refresh cache

        Returns:
            ProxiedContent object
        """
        if not self._proxy:
            raise MediaError("Proxy not available")
        
        return self._proxy.fetch(url, force_refresh)

    def get_proxied_content(self, url: str) -> Tuple[bytes, str]:
        """
        Get proxied content.

        Args:
            url: External URL

        Returns:
            Tuple of (content bytes, content_type)
        """
        if not self._proxy:
            raise MediaError("Proxy not available")
        
        return self._proxy.get_content(url)

    def scan_file(self, file_id: int) -> Tuple[ScanStatus, Optional[str]]:
        """
        Scan file for malware.

        Args:
            file_id: File ID

        Returns:
            Tuple of (ScanStatus, threat_name or None)
        """
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        
        if not self._scanner:
            return ScanStatus.SKIPPED, None
        
        file_data = self._storage.retrieve(file.storage_path)
        status, result = self._scanner.scan_bytes(file_data)
        
        self._db.execute(
            "UPDATE media_files SET scan_status = ?, scan_result = ? WHERE id = ?",
            (status.value, result, file_id)
        )
        
        return status, result

    def _row_to_media_file(self, row) -> MediaFile:
        """Convert database row to MediaFile."""
        import json
        
        metadata = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except Exception:
                pass
        
        return MediaFile(
            id=row["id"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            content_type=row["content_type"],
            size=row["size"],
            media_type=MediaType(row["media_type"]),
            storage_backend=StorageBackend(row["storage_backend"]),
            storage_path=row["storage_path"],
            url=self._storage.get_url(row["storage_path"]),
            checksum=row["checksum"],
            uploaded_by=row["uploaded_by"],
            uploaded_at=row["uploaded_at"],
            metadata=metadata,
            scan_status=ScanStatus(row["scan_status"]),
            scan_result=row["scan_result"],
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
        )
