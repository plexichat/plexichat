"""
Media manager - Core business logic for media operations.

Handles file uploads, processing, storage, and URL signing.
"""

import os
import time
import hashlib
import mimetypes
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, BinaryIO, Tuple

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager
from src.core import ratelimit

from .models import (
    MediaFile,
    MediaType,
    StorageBackend,
    ScanStatus,
    UploadResult,
    AttachmentData,
    VideoMetadata,
    SignedUrl,
    ProxiedContent,
)
from .exceptions import (
    MediaError,
    FileUploadError,
    FileSizeError,
    FileTypeError,
    ImageProcessingError,
    PermissionDeniedError,
)
from .storage import (
    LocalStorage,
    S3Storage,
    DatabaseStorage,
    StorageBackendBase,
    wrap_storage_with_encryption,
)
from .processing import ImageProcessor, VideoProcessor
from .security import UrlSigner, MalwareScanner, ExternalProxy
from .security.validation import BLOCKED_EXTENSIONS, BLOCKED_MIME_TYPES


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


class MediaManager(BaseManager):
    """Core media manager handling all operations."""

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename to prevent security issues.

        Removes:
        - Directory traversal (.., /)
        - Null bytes
        - Control characters
        - Excessive length
        """
        # Normalize path separators first (handle both Unix and Windows paths)
        filename = filename.replace("\\", "/")

        # Remove directory separators and path components
        filename = os.path.basename(filename)

        # Remove path traversal attempts that might remain
        filename = filename.replace("..", "")

        # Remove null bytes and control characters
        filename = "".join(c for c in filename if ord(c) >= 32 and ord(c) != 127)

        # Limit length to 250 characters
        if len(filename) > 250:
            name, ext = os.path.splitext(filename)
            max_name_len = 250 - len(ext)
            filename = name[:max_name_len] + ext

        # Ensure not empty
        if not filename or filename.strip() == ".":
            filename = f"unnamed_file_{self._get_timestamp() // 1000}"

        return filename

    def __init__(self, db, messaging_module=None):
        """
        Initialize the media manager.

        Args:
            db: Database instance (must be connected)
            messaging_module: Optional messaging module for attachment integration
        """
        super().__init__(db)
        self._messaging = messaging_module
        self._config = self._load_config()
        self._lock = threading.Lock()

        self._storage = self._init_storage()
        self._db_storage = self._init_db_storage()  # For auto-routing
        self._image_processor = self._init_image_processor()
        self._video_processor = self._init_video_processor()
        self._url_signer = self._init_url_signer()
        self._scanner = self._init_scanner()
        self._proxy = self._init_proxy()
        self._executor = ThreadPoolExecutor(max_workers=10)

        # Initialize deduplication once
        from .deduplication import setup as dedup_setup, DeduplicationManager

        dedup_setup(db)
        self._dedup_manager = DeduplicationManager(db)

        # Initialize compression once
        try:
            from .compression import CompressionManager

            self._compression_manager = CompressionManager()
        except ImportError:
            self._compression_manager = None


        logger.info("Media module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load media configuration from global config."""
        # Try 'media' first, then 'storage' as fallback
        cfg = config.get("media")
        if cfg is None:
            cfg = config.get("storage", {})

        if not isinstance(cfg, dict):
            cfg = {}

        # Ensure critical sections exist for robust behavior and patching in tests
        if "size_limits" not in cfg:
            cfg["size_limits"] = DEFAULT_SIZE_LIMITS.copy()
        if "allowed_types" not in cfg:
            cfg["allowed_types"] = DEFAULT_ALLOWED_TYPES.copy()
        if "rate_limit" not in cfg:
            cfg["rate_limit"] = {"enabled": True}
        if "image_processing" not in cfg:
            cfg["image_processing"] = {
                "max_thumbnail_requests_per_minute": 60,
                "thumbnail_sizes": DEFAULT_THUMBNAIL_SIZES,
            }
        if "auto_route_to_database" not in cfg:
            cfg["auto_route_to_database"] = {"enabled": False}

        return cfg

    def _init_storage(self) -> StorageBackendBase:
        """Initialize primary storage backend."""
        backend = self._config.get("storage_backend", "local")
        encrypt_at_rest = self._config.get("encrypt_at_rest", True)

        if backend == "s3":
            storage = S3Storage(
                bucket=self._config.get("s3_bucket", "plexichat-media"),
                access_key=self._config.get("s3_access_key", ""),
                secret_key=self._config.get("s3_secret_key", ""),
                region=self._config.get("s3_region", "us-east-1"),
                endpoint_url=self._config.get("s3_endpoint") or None,
                public_url=self._config.get("s3_public_url") or None,
            )
        elif backend == "database":
            storage = DatabaseStorage(
                db=self._db,
                base_url=self._config.get("database_url", "/api/v1/media/blob"),
                max_size=self._config.get("database_max_size", 512 * 1024),
            )
        else:
            storage = LocalStorage(
                base_path=self._config.get("local_path", "uploads"),
                base_url=self._config.get("local_url", "/media"),
            )

        # Wrap with encryption if enabled
        if encrypt_at_rest:
            # Task #6: Ensure media encryption uses app's signing keys if no env var set
            signing_key = self._config.get("signing_key")
            if signing_key and signing_key not in [
                "",
                "CHANGE_THIS_SIGNING_KEY",
                "change-me",
                "changeme",
            ]:
                if "PLEXICHAT_MEDIA_KEY" not in os.environ:
                    # Derive a 32-byte key from the signing key for initial keyring setup
                    derived_key = hashlib.sha256(signing_key.encode()).digest()
                    import base64

                    os.environ["PLEXICHAT_MEDIA_KEY"] = base64.b64encode(
                        derived_key
                    ).decode()
                    logger.debug("Derived PLEXICHAT_MEDIA_KEY from signing_key")

            storage = wrap_storage_with_encryption(storage, enabled=True)
            logger.info(f"File encryption at rest enabled for {backend} storage")

        return storage

    def _init_db_storage(self) -> Optional[StorageBackendBase]:
        """Initialize database storage for auto-routing (if enabled and not primary)."""
        auto_route = self._config.get("auto_route_to_database", {})
        primary_backend = self._config.get("storage_backend", "local")
        encrypt_at_rest = self._config.get("encrypt_at_rest", True)

        # Only create separate DB storage if auto-routing is enabled and DB isn't primary
        if auto_route.get("enabled", False) and primary_backend != "database":
            storage = DatabaseStorage(
                db=self._db,
                base_url=self._config.get("database_url", "/api/v1/media/blob"),
                max_size=auto_route.get("max_size", 512 * 1024),
            )

            # Wrap with encryption if enabled (Fix: ensure auto-routed DB storage is also encrypted)
            if encrypt_at_rest:
                storage = wrap_storage_with_encryption(storage, enabled=True)
                logger.debug("Encrypted database storage initialized for auto-routing")

            return storage
        return None

    def _check_rate_limit(self, user_id: int, file_size: int) -> None:
        """
        Check if user is within rate limits.

        Raises:
            MediaError: If rate limit exceeded
        """
        rate_config = self._config.get("rate_limit", {})
        if not rate_config.get("enabled", False):
            return

        # 1. Check frequency using core ratelimit module
        rl_result = ratelimit.check_rate_limit(
            user_id=user_id, route="POST /media/upload"
        )
        if not rl_result.allowed:
            raise MediaError(
                f"Upload rate limit exceeded. Please try again in {int(rl_result.retry_after or 1)}s"
            )

        # 2. Check daily size limit (still uses database for now as core doesn't handle size costs yet)
        now_seconds = self._get_timestamp() // 1000
        day_window = now_seconds - (now_seconds % 86400)

        # Use existing buckets table if we migrated, or keep a simpler check
        # For simplicity, we keep using the new ratelimit_buckets for daily size via a special key
        manager = ratelimit.get_manager()
        size_key = f"media:size:day:{user_id}:{day_window}"

        # Atomic increment of size
        current_size = manager.increment_custom_usage(size_key, "total_size", file_size)

        max_daily_size = rate_config.get("max_total_size_per_day", 512 * 1024 * 1024)
        if current_size > max_daily_size:
            # Rollback the increment if failed?
            # Better to check first, then increment. But for safety:
            manager.increment_custom_usage(size_key, "total_size", -file_size)
            remaining = max(0, max_daily_size - (current_size - file_size))
            raise MediaError(
                f"Daily upload limit exceeded. Remaining: {remaining // (1024 * 1024)}MB"
            )

    def _get_daily_size(self, user_id: int, day_window: int) -> int:
        """Get total upload size for day."""
        manager = ratelimit.get_manager()
        size_key = f"media:size:day:{user_id}:{day_window}"
        # We need a way to read custom usage without incrementing
        # Since we don't have a public read method, we can increment by 0
        return manager.increment_custom_usage(size_key, "total_size", 0)

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
                buffer_size=self._config.get("proxy_buffer_size", 65536),
            )
        except Exception as e:
            logger.warning(f"External proxy unavailable: {e}")
            return None

    def _detect_content_type(self, file_data: bytes, fallback: str) -> str:
        """Detect actual content type from magic bytes."""
        # Common magic byte signatures
        signatures = {
            b"\xff\xd8\xff": "image/jpeg",
            b"\x89PNG\r\n\x1a\n": "image/png",
            b"GIF87a": "image/gif",
            b"GIF89a": "image/gif",
            b"RIFF": "image/webp",  # Basic check, should check for WEBP
            b"%PDF": "application/pdf",
        }

        for sig, mime in signatures.items():
            if file_data.startswith(sig):
                return mime
        return fallback

    def _get_storage_for_file(
        self, content_type: str, size: int
    ) -> Tuple[StorageBackendBase, str]:
        """Determine the correct storage backend for a file."""
        auto_route = self._config.get("auto_route_to_database", {})

        # Check if we should route to database (small files if enabled)
        if (
            auto_route.get("enabled", False)
            and self._db_storage
            and size <= auto_route.get("max_size", 512 * 1024)
        ):
            # Additional check: only route text or specific small types to DB by default
            # unless configured otherwise
            allowed_types = auto_route.get(
                "allowed_types",
                ["text/plain", "application/json", "application/javascript"],
            )
            if content_type in allowed_types or "*" in allowed_types:
                return self._db_storage, "database"

        # Fall back to primary storage
        return self._storage, self._config.get("storage_backend", "local")

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

    def _validate_magic_bytes(self, file_data: bytes, content_type: str) -> bool:
        """
        Validate file content matches declared content type using magic bytes.

        This prevents MIME type spoofing attacks where a malicious file
        is uploaded with a fake content type.

        Args:
            file_data: Raw file bytes
            content_type: Declared content type

        Returns:
            True if magic bytes match content type, False otherwise
        """
        # Magic byte signatures for common file types
        magic_signatures = {
            # Images
            "image/jpeg": [b"\xff\xd8\xff"],
            "image/png": [b"\x89PNG\r\n\x1a\n"],
            "image/gif": [b"GIF87a", b"GIF89a"],
            "image/webp": [b"RIFF"],  # RIFF....WEBP
            "image/bmp": [b"BM"],
            "image/tiff": [b"II*\x00", b"MM\x00*"],
            # Videos
            "video/mp4": [
                b"\x00\x00\x00\x18ftypmp4",
                b"\x00\x00\x00\x1cftypmp4",
                b"\x00\x00\x00 ftypisom",
                b"ftyp",
            ],
            "video/webm": [b"\x1a\x45\xdf\xa3"],
            "video/quicktime": [b"\x00\x00\x00\x14ftypqt", b"ftypqt"],
            # Audio
            "audio/mpeg": [b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2", b"ID3"],
            "audio/ogg": [b"OggS"],
            "audio/wav": [b"RIFF"],  # RIFF....WAVE
            "audio/webm": [b"\x1a\x45\xdf\xa3"],
            # Documents
            "application/pdf": [b"%PDF"],
            "application/zip": [b"PK\x03\x04", b"PK\x05\x06"],
            # Text types don't have magic bytes, allow through
            "text/plain": [],
            "text/markdown": [],
            "text/csv": [],
            "application/json": [],
        }

        ct_lower = content_type.lower()

        # If we don't have signatures for this type, allow through (unknown type)
        if ct_lower not in magic_signatures:
            return True

        signatures = magic_signatures[ct_lower]

        # Text types have no magic bytes -> valid
        if not signatures:
            return True

        # If file is too short to match ANY signature for this type, and it HAS enforced signatures, it's invalid
        # Exception: some signatures might be longer than the file, but we should check if file matches prefix?
        # No, magic bytes usually appear at start. If file < signature, it doesn't match.
        # Check against shortest signature
        shortest_sig_len = min(len(s) for s in signatures) if signatures else 0
        if len(file_data) < shortest_sig_len:
            return False

        # Check if file starts with any valid signature
        for sig in signatures:
            if file_data.startswith(sig):
                return True
            # Special case for MP4/MOV - ftyp can be at offset 4
            if (
                ct_lower in ("video/mp4", "video/quicktime")
                and len(file_data) >= 12
                and b"ftyp" in file_data[:12]
            ):
                return True

        return False

    def _validate_content_type(self, content_type: str, media_type: MediaType):
        """Validate content type is allowed."""
        allowed = self._config.get("allowed_types", DEFAULT_ALLOWED_TYPES)
        type_key = media_type.value

        ct_lower = content_type.lower()

        # Check against global blocked MIME types first
        if ct_lower in BLOCKED_MIME_TYPES:
            raise FileTypeError(
                f"File type '{content_type}' is blocked for security reasons.",
                content_type,
                ["Contact an administrator for more information"],
            )

        if type_key in allowed:
            if ct_lower not in allowed[type_key]:
                # If it's a generic type but the media type is more specific, it might be allowed elsewhere
                # but we strictly check the whitelist here
                raise FileTypeError(
                    f"Content type '{content_type}' is not allowed for {type_key} uploads.",
                    content_type,
                    allowed[type_key],
                )
        elif type_key == "other":
            # For 'other' category, we only allow what's explicitly in the whitelist
            other_allowed = allowed.get("other", [])
            if (
                other_allowed
                and ct_lower not in other_allowed
                and "*" not in other_allowed
            ):
                raise FileTypeError(
                    f"File type '{content_type}' is not supported.",
                    content_type,
                    other_allowed,
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
        filename = self._sanitize_filename(filename)
        if not content_type:
            content_type, _ = mimetypes.guess_type(filename)
            content_type = content_type or "application/octet-stream"

        media_type = self._detect_media_type(content_type)
        file_size = len(file_data)

        # Pre-validation
        self._validate_content_type(content_type, media_type)
        self._validate_file_size(file_size, media_type)

        # Check rate limits
        self._check_rate_limit(user_id, file_size)

        # Delegate to internal logic
        return self._do_upload(
            user_id=user_id,
            file_data=file_data,
            filename=filename,
            content_type=content_type,
            media_type=media_type,
        )

    def _do_upload(
        self,
        user_id: int,
        file_data: bytes,
        filename: str,
        content_type: Optional[str] = None,
        media_type: Optional[MediaType] = None,
    ) -> UploadResult:
        """
        Internal upload logic.
        """
        if not content_type:
            content_type, _ = mimetypes.guess_type(filename)
            content_type = content_type or "application/octet-stream"

        # Detect actual content type from bytes to prevent spoofing
        detected_type = self._detect_content_type(file_data, content_type)
        generic_types = ["application/octet-stream", "text/plain", "application/binary"]

        if not content_type or content_type.lower() in generic_types:
            content_type = detected_type
            # Re-detect media type if it changed
            media_type = self._detect_media_type(content_type)
        elif detected_type != content_type:
            logger.info(f"Mismatch: claimed {content_type}, detected {detected_type}")
            # We keep claimed type so _validate_magic_bytes will fail it below
            pass

        if not media_type:
            media_type = self._detect_media_type(content_type)

        file_size = len(file_data)

        # SECURITY: Check for blocked executable extensions
        ext = os.path.splitext(filename.lower())[1]
        if ext in BLOCKED_EXTENSIONS:
            raise FileTypeError(
                f"File type not allowed: {ext}",
                content_type,
                ["Executable and script files are blocked for security"],
            )

        # SECURITY: Check for blocked MIME types
        if content_type.lower() in BLOCKED_MIME_TYPES:
            raise FileTypeError(
                f"Content type not allowed: {content_type}",
                content_type,
                ["This content type is blocked for security"],
            )

        # SECURITY: Validate magic bytes match content type (prevents MIME spoofing)
        if not self._validate_magic_bytes(file_data, content_type):
            logger.warning(
                f"Magic byte validation failed for {filename} (claimed: {content_type})"
            )
            raise FileTypeError(
                f"File content does not match declared type: {content_type}",
                content_type,
                ["File signature mismatch - content does not match declared MIME type"],
            )

        # SECURITY: Check for blocked/duplicate content using deduplication module
        dedup_result = None
        if self._dedup_manager:
            try:
                dedup_result = self._dedup_manager.check_duplicate(
                    file_data, content_type
                )

                if dedup_result.is_blocked:
                    logger.warning(
                        f"Blocked content upload attempt by user {user_id}: {dedup_result.block_reason}"
                    )
                    raise FileUploadError(
                        f"This content has been blocked: {dedup_result.block_reason}",
                        filename,
                    )
            except Exception as e:
                logger.warning(f"Deduplication check failed: {e}")
                dedup_result = None

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

        # OPTIMIZATION: Apply compression if enabled
        compressed_data = file_data
        compression_applied = False
        if self._compression_manager and self._compression_manager.is_enabled():
            try:
                compression_result = self._compression_manager.compress(
                    file_data, content_type
                )
                if compression_result.success and compression_result.data:
                    # Only use compressed version if it's actually smaller
                    if compression_result.compressed_size < file_size:
                        compressed_data = compression_result.data
                        compression_applied = True
                        logger.debug(
                            f"Compression applied: {file_size} -> {compression_result.compressed_size} bytes ({compression_result.savings_percent:.1f}% saved)"
                        )
                        # Update content type if format changed
                        if compression_result.format:
                            content_type = compression_result.format
            except Exception as e:
                logger.warning(f"Compression failed, using original: {e}")

        # Use compressed data for storage
        final_data = compressed_data
        final_size = len(final_data)

        # Determine storage backend (auto-routing for small text files)
        storage, storage_backend = self._get_storage_for_file(content_type, final_size)

        storage_path = self._generate_storage_path(filename, media_type)
        checksum = self._compute_checksum(final_data)

        storage.store(final_data, storage_path, content_type)

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
            except ImageProcessingError as e:
                # Security-related errors (decompression bombs, excessive dimensions) should fail the upload
                logger.warning(f"Image security validation failed: {e}")
                raise FileUploadError(str(e), filename)
            except Exception as e:
                logger.warning(f"Failed to extract image metadata: {e}")
        elif (
            media_type == MediaType.VIDEO
            and self._video_processor
            and self._video_processor.is_available()
        ):
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
                final_size,
                media_type.value,
                storage_backend,  # Use the actual backend (may be auto-routed)
                storage_path,
                checksum,
                user_id,
                now,
                metadata_json,
                scan_status.value,
                scan_result,
            ),
        )

        # Register file hash for deduplication tracking
        if dedup_result and self._dedup_manager is not None:
            try:
                # Compute pHash for images to enable similarity detection
                phash = self._dedup_manager.compute_phash(final_data, content_type)

                self._dedup_manager.register_file(
                    hash_value=checksum,
                    file_size=final_size,
                    content_type=content_type,
                    storage_path=storage_path,
                    storage_backend=storage_backend,
                    timestamp=now,
                    phash_value=phash,
                )
            except Exception as e:
                logger.warning(f"Failed to register file hash: {e}")

        thumbnails = {}
        if media_type == MediaType.IMAGE and self._image_processor:
            thumbnails = self._generate_thumbnails(file_id, final_data)

        # Use our API proxy URL instead of direct storage URL
        # This ensures auth checks and signed URL redirection for S3
        # Use filename as stored in DB (os.path.basename(storage_path))
        stored_filename = os.path.basename(storage_path)
        url = f"/api/v1/media/attachments/{stored_filename}"

        compression_info = (
            f", compressed from {file_size}" if compression_applied else ""
        )
        logger.debug(
            f"File {file_id} uploaded by user {user_id}: {filename} (backend: {storage_backend}, size: {final_size}{compression_info})"
        )

        return UploadResult(
            file_id=file_id,
            filename=filename,
            content_type=content_type,
            size=final_size,
            url=url,
            thumbnails=thumbnails,
            metadata=metadata,
            checksum=checksum,  # Include hash for client-side reporting
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

            size: File size in bytes

        Returns:
            UploadResult with file info and URLs
        """
        filename = self._sanitize_filename(filename)
        media_type = self._detect_media_type(content_type)

        self._validate_content_type(content_type, media_type)
        self._validate_file_size(size, media_type)

        with self._lock:
            # Check rate limits under lock
            self._check_rate_limit(user_id, size)

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
                ),
            )

        # Use our API proxy URL instead of direct storage URL
        stored_filename = os.path.basename(storage_path)
        url = f"/api/v1/media/attachments/{stored_filename}"

        logger.debug(
            f"File {file_id} uploaded via stream by user {user_id}: {filename}"
        )

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

            def store_thumb(size, data_tuple):
                thumb_data, width, height = data_tuple
                thumb_path = f"thumbnails/{file_id}/{size}.jpg"
                self._storage.store(thumb_data, thumb_path, "image/jpeg")

                thumb_id = self._generate_id()
                now = self._get_timestamp()

                self._db.execute(
                    """INSERT INTO media_thumbnails
                       (id, media_file_id, size, width, height, storage_path, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (thumb_id, file_id, size, width, height, thumb_path, now),
                )
                return size, self._storage.get_url(thumb_path)

            # Use the shared class executor for thumbnail storage
            futures = [
                self._executor.submit(store_thumb, size, data)
                for size, data in results.items()
            ]
            for future in futures:
                try:
                    size, url = future.result()
                    thumbnails[size] = url
                except Exception as fe:
                    logger.warning(f"Failed to store thumbnail size: {fe}")

        except Exception as e:
            logger.warning(f"Failed to generate thumbnails: {e}")

        return thumbnails

    def get_file(self, file_id: int) -> Optional[MediaFile]:
        """Get file by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE id = ? AND deleted = 0", (file_id,)
        )

        if not row:
            return None

        return self._row_to_media_file(row)

    def get_file_by_filename(self, filename: str) -> Optional[MediaFile]:
        """Get file by stored filename."""
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE filename = ? AND deleted = 0",
            (filename,)
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

        # Use the correct storage backend for this file
        storage = self._get_storage_by_backend(file.storage_backend.value)
        data = storage.retrieve(file.storage_path)
        return data, file.content_type

    def get_file_stream(self, file_id: int) -> Tuple[BinaryIO, int, str]:
        """
        Get file data as a stream.

        Args:
            file_id: File ID

        Returns:
            Tuple of (file stream, size, content_type)
        """
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")

        # Use the correct storage backend for this file
        storage = self._get_storage_by_backend(file.storage_backend.value)
        stream, size = storage.retrieve_stream(file.storage_path)
        return stream, size, file.content_type

    def get_file_stream_optimized(
        self, path: str, content_type: str, backend: str
    ) -> Tuple[BinaryIO, int, str]:
        """
        Get file data as a stream directly (avoids DB lookup).

        Args:
            path: Storage path
            content_type: Content type
            backend: Storage backend value (s3, local, database)

        Returns:
            Tuple of (file stream, size, content_type)
        """
        storage = self._get_storage_by_backend(backend)
        stream, size = storage.retrieve_stream(path)
        return stream, size, content_type

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
            (now, file_id),
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

    def get_rate_limit_status(self, user_id: int) -> Dict[str, Any]:
        """
        Get current rate limit status for a user.
        """
        rate_config = self._config.get("rate_limit", {})
        if not rate_config.get("enabled", True):
            return {"enabled": False}

        now_seconds = self._get_timestamp() // 1000
        from src.core.ratelimit.models import BucketType

        manager = ratelimit.get_manager()

        # Get usage from core module
        bucket_key = manager._generate_bucket_key(
            BucketType.ROUTE, user_id=user_id, route="POST /media/upload"
        )
        bucket_info = ratelimit.get_bucket_info(bucket_key)

        # Daily size usage
        day_window = now_seconds - (now_seconds % 86400)
        day_size = self._get_daily_size(user_id, day_window)

        # We assume TOKEN_BUCKET or SLIDING_WINDOW usage
        used_minute = bucket_info.request_count if bucket_info else 0
        used_hour = bucket_info.hourly_count if bucket_info else 0

        max_per_minute = rate_config.get("uploads_per_minute", 10)
        max_per_hour = rate_config.get("uploads_per_hour", 100)
        max_daily_size = rate_config.get("max_total_size_per_day", 512 * 1024 * 1024)

        return {
            "enabled": True,
            "minute": {
                "used": used_minute,
                "limit": max_per_minute,
                "remaining": max(0, max_per_minute - used_minute),
                "resets_in": 60 - (now_seconds % 60),
            },
            "hour": {
                "used": used_hour,
                "limit": max_per_hour,
                "remaining": max(0, max_per_hour - used_hour),
                "resets_in": 3600 - (now_seconds % 3600),
            },
            "day": {
                "used_bytes": day_size,
                "limit_bytes": max_daily_size,
                "remaining_bytes": max(0, max_daily_size - day_size),
                "resets_in": 86400 - (now_seconds % 86400),
            },
        }

    def sign_url(
        self,
        file_id: int,
        expires_in: Optional[int] = None,
        params: Optional[dict] = None,
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
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")

        # Get correct storage for this file
        storage = self._get_storage_by_backend(file.storage_backend.value)

        # Check if file is encrypted (requires server-side decryption)
        is_encrypted = storage.is_encrypted(file.storage_path)

        # If encrypted, we MUST serve it through our API proxy for decryption
        if is_encrypted:
            # Sign our proxy URL
            proxy_url = f"/api/v1/media/attachments/{file.filename}"
            return self._url_signer.sign_url(proxy_url, file_id, expires_in)

        # If using S3 and not encrypted, prefer native S3 presigning
        if file.storage_backend == StorageBackend.S3:
            # Look for native signing capability (including through wrappers)
            if hasattr(storage, "generate_presigned_url"):
                url = storage.generate_presigned_url(  # type: ignore
                    file.storage_path, expires_in or 3600, params=params
                )
                return SignedUrl(
                    url=url,
                    expires_at=int(time.time() * 1000) + ((expires_in or 3600) * 1000),
                    signature="native",
                    file_id=file_id,
                )

            # If S3 but no native signing available (unlikely), we MUST proxy it
            # because direct S3 URLs will fail without a native signature
            logger.warning(
                f"S3 native signing unavailable for {file.filename}, falling back to proxy"
            )
            proxy_url = f"/api/v1/media/attachments/{file.filename}"
            return self._url_signer.sign_url(proxy_url, file_id, expires_in)

        # Fallback to signing the default URL (Local/Database storage)
        url = storage.get_url(file.storage_path)
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
            "SELECT * FROM media_thumbnails WHERE media_file_id = ?", (file_id,)
        )

        return {row["size"]: self._storage.get_url(row["storage_path"]) for row in rows}

    def create_thumbnail(
        self,
        file_id: int,
        size: int,
        user_id: Optional[int] = None,
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
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.IMAGE:
            return None

        if not self._image_processor:
            return None

        # Check rate limit if user_id provided
        if user_id is not None:
            rl_result = ratelimit.check_rate_limit(
                user_id=user_id, route="THUMBNAIL_GEN"
            )
            if not rl_result.allowed:
                raise MediaError(
                    f"Thumbnail generation rate limit exceeded. Please try again in {int(rl_result.retry_after or 1)}s"
                )

        existing = self._db.fetch_one(
            "SELECT * FROM media_thumbnails WHERE media_file_id = ? AND size = ?",
            (file_id, size),
        )

        if existing:
            return self._storage.get_url(existing["storage_path"])

        try:
            image_data = self._storage.retrieve(file.storage_path)
            thumb_data, width, height = self._image_processor.create_thumbnail(
                image_data, size
            )

            thumb_path = f"thumbnails/{file_id}/{size}.jpg"
            self._storage.store(thumb_data, thumb_path, "image/jpeg")

            thumb_id = self._generate_id()
            now = self._get_timestamp()

            self._db.execute(
                """INSERT INTO media_thumbnails
                   (id, media_file_id, size, width, height, storage_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (thumb_id, file_id, size, width, height, thumb_path, now),
            )

            return self._storage.get_url(thumb_path)
        except MediaError:
            raise  # Re-raise rate limit errors
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
            (status.value, result, file_id),
        )

        return status, result

    def check_file_access(self, filename: str, user_id: int) -> bool:
        """
        Check if a user has permission to access a media file.

        Access is granted if:
        1. User is the original uploader
        2. File is an attachment in a message within a conversation the user is in
        3. File is a public resource (current avatar or server icon for shared contexts)

        Args:
            filename: The unique filename stored in media_files
            user_id: ID of the user requesting access

        Returns:
            True if access granted, False otherwise
        """
        # 1. Check if user is the uploader (fast path)
        row = self._db.fetch_one(
            "SELECT id, uploaded_by FROM media_files WHERE filename = ? AND deleted = 0",
            (filename,),
        )
        if not row:
            logger.debug(f"check_file_access: file {filename} not found in media_files")
            return False

        uploader_id = int(row["uploaded_by"])
        if uploader_id == user_id:
            return True

        logger.debug(f"check_file_access: file={filename}, user={user_id}, uploader={uploader_id}")
        file_id = row["id"]

        # 2. Check if it's a message attachment
        # We look for the file_id or filename in msg_attachments
        # Also handle potential URL encoding or full paths in the URL column
        query = """
            SELECT m.conversation_id 
            FROM msg_messages m
            JOIN msg_attachments a ON m.id = a.message_id
            WHERE (a.filename = ? OR a.url LIKE '%' || ? OR a.metadata LIKE '%' || ?) AND a.deleted = 0
        """
        # We search for filename, filename in URL, and file_id in metadata JSON string
        search_filename = filename
        if "/" in search_filename:
            search_filename = os.path.basename(search_filename)

        rows = self._db.fetch_all(query, (search_filename, search_filename, f'%{file_id}%'))
        logger.debug(f"check_file_access: found {len(rows) if rows else 0} potential attachment rows for {search_filename}")

        if rows and self._messaging:
            for r in rows:
                conv_id = r["conversation_id"]
                is_p = False
                try:
                    # Robust check for is_participant (module or manager instance)
                    if hasattr(self._messaging, "is_participant"):
                        is_p = self._messaging.is_participant(conv_id, user_id)
                    else:
                        is_p = self._messaging.get_manager().is_participant(conv_id, user_id)
                    logger.debug(f"check_file_access: user {user_id} in conversation {conv_id} -> {is_p}")
                except Exception as e:
                    logger.warning(f"Failed to check participation for {user_id} in {conv_id}: {e}")

                if is_p:
                    return True

        # 3. Check if it's a current avatar or server icon
        # Avatars are generally public to people who can see the user
        avatar_row = self._db.fetch_one(
            "SELECT 1 FROM auth_users WHERE avatar_url LIKE '%' || ?", (search_filename,)
        )
        if avatar_row:
            return True

        # Server icons are public to members of that server
        icon_query = """
            SELECT 1 FROM srv_servers s
            JOIN srv_members m ON s.id = m.server_id
            WHERE s.icon_url LIKE '%' || ? AND m.user_id = ? AND s.deleted = 0
        """
        icon_row = self._db.fetch_one(icon_query, (search_filename, user_id))
        if icon_row:
            return True

        logger.debug(f"check_file_access: Access denied for user {user_id} to file {filename}")
        return False

    def _get_storage_by_backend(self, backend: str) -> StorageBackendBase:
        """Get storage instance for a specific backend type."""
        if backend == "database":
            # Return DB storage if available, otherwise fall back to primary
            return self._db_storage if self._db_storage else self._storage
        elif backend == self._config.get("storage_backend", "local"):
            return self._storage
        else:
            # Backend doesn't match current config - file may have been uploaded
            # with different settings. Try to use primary storage.
            logger.warning(f"File backend '{backend}' differs from current config")
            return self._storage

    def _row_to_media_file(self, row) -> MediaFile:
        """Convert database row to MediaFile."""
        import json

        metadata = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except Exception:
                pass

        # Get the correct storage for this file's backend
        backend = row["storage_backend"]
        storage = self._get_storage_by_backend(backend)

        return MediaFile(
            id=row["id"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            content_type=row["content_type"],
            size=row["size"],
            media_type=MediaType(row["media_type"]),
            storage_backend=StorageBackend(backend),
            storage_path=row["storage_path"],
            url=storage.get_url(row["storage_path"]),
            checksum=row["checksum"],
            uploaded_by=row["uploaded_by"],
            uploaded_at=row["uploaded_at"],
            metadata=metadata,
            scan_status=ScanStatus(row["scan_status"]),
            scan_result=row["scan_result"],
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
        )
