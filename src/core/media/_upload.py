# pyright: reportAttributeAccessIssue=false
"""
Upload pipeline (upload_file, upload_stream, _do_upload, upload_attachment) mixed into MediaManager.

All optimisation work from prior commits is preserved:
- Small files (≤8 MB) read directly into memory, avoiding disk I/O.
- Thumbnails deferred to fire-and-forget background thread.
- Malware scan runs in parallel with compression + metadata.
- Exact-hash duplicate check is inline (fast); pHash similarity is background-only.
- Rate-limit update is fire-and-forget.
"""

import os
import mimetypes
import hashlib
import json
import tempfile
import logging
from typing import Optional, BinaryIO, Tuple

from .models import MediaType, ScanStatus, UploadResult, AttachmentData
from .exceptions import (
    FileUploadError,
    FileSizeError,
    FileTypeError,
    ImageProcessingError,
)
from .security.validation import BLOCKED_EXTENSIONS, BLOCKED_MIME_TYPES

logger = logging.getLogger(__name__)


class _UploadMixin:
    """Upload pipeline methods mixed into MediaManager."""

    # ── stream I/O helpers ────────────────────────────────────────────────────

    def _read_stream_to_temp(self, stream: BinaryIO) -> Tuple[str, int, str, bytes]:
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        bytes_read = 0
        hasher = hashlib.sha256()
        header = b""
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                if not header:
                    header = chunk[:64]
                temp_file.write(chunk)
                hasher.update(chunk)
                bytes_read += len(chunk)
        finally:
            temp_file.flush()
            temp_file.close()
        return temp_file.name, bytes_read, hasher.hexdigest(), header

    def _read_stream_to_memory(
        self, stream: BinaryIO, max_bytes: int
    ) -> Tuple[bytes, int, str, bytes]:
        """Read stream into memory (no temp file), computing hash inline."""
        chunks = []
        bytes_read = 0
        hasher = hashlib.sha256()
        header = b""
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            if not header:
                header = chunk[:64]
            hasher.update(chunk)
            bytes_read += len(chunk)
            if bytes_read > max_bytes:
                raise FileSizeError(
                    f"File exceeds in-memory limit of {max_bytes} bytes",
                    max_bytes,
                    bytes_read,
                )
            chunks.append(chunk)
        file_data = b"".join(chunks)
        return file_data, bytes_read, hasher.hexdigest(), header

    # ── upload_file (bytes) ───────────────────────────────────────────────────

    def upload_file(
        self,
        user_id: int,
        file_data: bytes,
        filename: str,
        content_type: Optional[str] = None,
    ) -> UploadResult:
        """Upload a file from bytes."""
        filename = self._sanitize_filename(filename)
        if not content_type:
            guessed_type, _ = mimetypes.guess_type(filename)
            content_type = guessed_type or "application/octet-stream"

        media_type = self._detect_media_type(content_type)
        file_size = len(file_data)

        self._validate_content_type(content_type, media_type)
        self._validate_file_size(file_size, media_type)
        self._check_rate_limit(user_id, file_size)

        result = self._do_upload(
            user_id=user_id,
            file_data=file_data,
            filename=filename,
            content_type=content_type,
            media_type=media_type,
        )
        return result

    # ── _do_upload (internal bytes path) ───────────────────────────────────────

    def _do_upload(
        self,
        user_id: int,
        file_data: bytes,
        filename: str,
        content_type: Optional[str] = None,
        media_type: Optional[MediaType] = None,
    ) -> UploadResult:
        if not content_type:
            content_type, _ = mimetypes.guess_type(filename)
            content_type = content_type or "application/octet-stream"

        detected_type = self._detect_content_type(file_data, content_type)
        generic_types = ["application/octet-stream", "text/plain", "application/binary"]
        if not content_type or content_type.lower() in generic_types:
            content_type = detected_type
            media_type = self._detect_media_type(content_type)
        elif detected_type != content_type:
            logger.info(f"Mismatch: claimed {content_type}, detected {detected_type}")

        if not media_type:
            media_type = self._detect_media_type(content_type)
        file_size = len(file_data)

        # Blocked extensions
        ext = os.path.splitext(filename.lower())[1]
        if ext in BLOCKED_EXTENSIONS:
            raise FileTypeError(
                f"File type not allowed: {ext}",
                content_type,
                ["Executable and script files are blocked for security"],
            )

        # Blocked MIME types
        if content_type and content_type.lower() in BLOCKED_MIME_TYPES:
            raise FileTypeError(
                f"Content type not allowed: {content_type}",
                content_type,
                ["This content type is blocked for security"],
            )

        # Magic-byte validation
        if not self._validate_magic_bytes(file_data, content_type):
            logger.warning(
                f"Magic byte validation failed for {filename} (claimed: {content_type})"
            )
            raise FileTypeError(
                f"File content does not match declared type: {content_type}",
                content_type,
                ["File signature mismatch - content does not match declared MIME type"],
            )

        # Compute checksum early for fast dedup
        checksum_raw = self._compute_checksum(file_data)

        # Fast inline dedup (exact-hash only — no O(n) pHash scans)
        dedup_result = None
        if self._dedup_manager:
            try:
                # 1. Check user blocked (1 query)
                is_blocked_user, block_reason = self._dedup_manager.is_user_blocked(
                    user_id
                )
                if is_blocked_user:
                    raise FileUploadError(
                        f"User blocked from uploads: {block_reason}", filename
                    )
                # 2. Check exact SHA256 block (1 query, no pHash scan)
                is_blocked_sha, sha_reason = self._dedup_manager.is_blocked(
                    checksum_raw, phash_value=None
                )
                if is_blocked_sha:
                    raise FileUploadError(
                        f"This content has been blocked: {sha_reason}", filename
                    )
                # 3. Check exact SHA256 duplicate (1 query)
                dedup_result = self._dedup_manager._check_exact_hash(checksum_raw)
            except FileUploadError:
                raise
            except Exception as e:
                logger.warning(f"Fast dedup check failed: {e}")

        # Malware scan
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

        # Compression
        compressed_data = file_data
        compression_applied = False
        if self._compression_manager and self._compression_manager.is_enabled():
            try:
                compression_result = self._compression_manager.compress(
                    file_data, content_type
                )
                if compression_result.success and compression_result.data:
                    if compression_result.compressed_size < file_size:
                        compressed_data = compression_result.data
                        compression_applied = True
                        logger.debug(
                            f"Compression applied: {file_size} -> "
                            f"{compression_result.compressed_size} bytes "
                            f"({compression_result.savings_percent:.1f}% saved)"
                        )
                        if compression_result.format:
                            content_type = compression_result.format
            except Exception as e:
                logger.warning(f"Compression failed, using original: {e}")

        final_data = compressed_data
        final_size = len(final_data)

        # Storage
        storage, storage_backend = self._get_storage_for_file(content_type, final_size)
        storage_path = self._generate_storage_path(filename, media_type)
        checksum = self._compute_checksum(final_data)
        storage.store(final_data, storage_path, content_type)

        file_id = self._generate_id()
        now = self._get_timestamp()

        # Metadata extraction
        assert media_type is not None, (
            "media_type must be resolved before metadata extraction"
        )
        assert content_type is not None, (
            "content_type must be resolved before metadata extraction"
        )
        metadata = self._extract_metadata(file_data, filename, media_type, content_type)

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
                (media_type.value if media_type else ""),
                storage_backend,
                storage_path,
                checksum,
                user_id,
                now,
                metadata_json,
                (scan_status.value if scan_status else ""),
                scan_result,
            ),
        )

        # Background dedup registration (fire-and-forget)
        if dedup_result and self._dedup_manager is not None:

            def _register_bg() -> None:
                try:
                    self._dedup_manager.register_file(
                        hash_value=checksum,
                        file_size=final_size,
                        content_type=content_type,
                        storage_path=storage_path,
                        storage_backend=storage_backend,
                        timestamp=now,
                        phash_value=None,
                    )
                except Exception as e:
                    logger.warning(f"Background dedup registration failed: {e}")

            self._executor.submit(_register_bg)

        # Thumbnails (background)
        if media_type == MediaType.IMAGE and self._image_processor:
            self._generate_thumbnails_background(file_id, final_data)

        # Background pHash dedup for images
        if self._dedup_manager and media_type == MediaType.IMAGE:
            self._executor.submit(
                self._do_background_phash_dedup,
                checksum_raw,
                file_data,
                content_type,
                user_id,
                file_size,
            )

        stored_filename = os.path.basename(storage_path)
        url = f"/api/v1/media/attachments/{stored_filename}"

        compression_info = (
            f", compressed from {file_size}" if compression_applied else ""
        )
        logger.debug(
            f"File {file_id} uploaded by user {user_id}: {filename} "
            f"(backend: {storage_backend}, size: {final_size}{compression_info})"
        )

        # Fire-and-forget: rate-limit update
        self._executor.submit(self._update_rate_limit, user_id, final_size)

        return UploadResult(
            file_id=file_id,
            filename=filename,
            content_type=content_type or "application/octet-stream",
            size=final_size,
            url=url,
            thumbnails={},
            metadata=metadata,
            checksum=checksum,
        )

    # ── upload_stream (stream path — heavily optimised) ────────────────────────

    def upload_stream(
        self,
        user_id: int,
        stream: BinaryIO,
        filename: str,
        content_type: str,
        size: int,
    ) -> UploadResult:
        filename = self._sanitize_filename(filename)
        if not content_type:
            guessed_type, _ = mimetypes.guess_type(filename)
            content_type = guessed_type or "application/octet-stream"

        media_type = self._detect_media_type(content_type)
        self._validate_content_type(content_type, media_type)
        if size and size > 0:
            self._validate_file_size(size, media_type)

        max_in_memory = self._config.get("stream_processing_max_bytes", 8 * 1024 * 1024)
        use_memory = size is not None and size > 0 and size <= max_in_memory
        temp_path = None
        file_data = None
        try:
            if use_memory:
                # OPTIMISATION: Read small files directly into memory — no disk I/O
                file_data, bytes_read, checksum_raw, header = (
                    self._read_stream_to_memory(stream, max_in_memory)
                )
            else:
                temp_path, bytes_read, checksum_raw, header = self._read_stream_to_temp(
                    stream
                )
            file_size = size if size and size > 0 else bytes_read
            if not size or size <= 0:
                self._validate_file_size(file_size, media_type)

            # Content-type detection
            detected_type = self._detect_content_type(header, content_type)
            generic_types = [
                "application/octet-stream",
                "text/plain",
                "application/binary",
            ]
            if not content_type or content_type.lower() in generic_types:
                content_type = detected_type
                media_type = self._detect_media_type(content_type)

            if content_type.lower() in BLOCKED_MIME_TYPES:
                raise FileTypeError(
                    f"Content type not allowed: {content_type}",
                    content_type,
                    ["This content type is blocked for security"],
                )

            ext = os.path.splitext(filename.lower())[1]
            if ext in BLOCKED_EXTENSIONS:
                raise FileTypeError(
                    f"File type not allowed: {ext}",
                    content_type,
                    ["Executable and script files are blocked for security"],
                )

            if not self._validate_magic_bytes(header, content_type):
                raise FileTypeError(
                    f"File content does not match declared type: {content_type}",
                    content_type,
                    [
                        "File signature mismatch - "
                        "content does not match declared MIME type"
                    ],
                )

            self._check_rate_limit(user_id, file_size)

            # ── Fast inline dedup (exact-hash only — no O(n) pHash scans) ──
            dedup_result = None
            if self._dedup_manager:
                try:
                    # 1. Check user blocked (1 query)
                    is_blocked_user, block_reason = self._dedup_manager.is_user_blocked(
                        user_id
                    )
                    if is_blocked_user:
                        raise FileUploadError(
                            f"User blocked from uploads: {block_reason}", filename
                        )
                    # 2. Check exact SHA256 block (1 query)
                    is_blocked_sha, sha_reason = self._dedup_manager.is_blocked(
                        checksum_raw, phash_value=None
                    )
                    if is_blocked_sha:
                        raise FileUploadError(
                            f"This content has been blocked: {sha_reason}", filename
                        )
                    # 3. Check exact SHA256 duplicate (1 query)
                    dedup_result = self._dedup_manager._check_exact_hash(checksum_raw)
                except FileUploadError:
                    raise
                except Exception as e:
                    logger.warning(f"Fast dedup check failed: {e}")

            # ── Fire-and-forget malware scan in parallel with compression ──
            scan_future = None
            if self._scanner and self._scanner.is_available():
                if use_memory and file_data is not None:
                    scan_future = self._executor.submit(
                        self._scanner.scan_bytes, file_data
                    )
                else:
                    assert temp_path is not None
                    scan_future = self._executor.submit(
                        self._scanner.scan_file, temp_path
                    )

            # Load file_data for compression/metadata if not already in memory
            if file_data is None and file_size <= max_in_memory and temp_path:
                with open(temp_path, "rb") as f:
                    file_data = f.read()

            # Compression (CPU-bound)
            final_data = None
            compression_applied = False
            if (
                self._compression_manager
                and self._compression_manager.is_enabled()
                and file_size <= max_in_memory
                and file_data is not None
            ):
                try:
                    compression_result = self._compression_manager.compress(
                        file_data, content_type
                    )
                    if compression_result.success and compression_result.data:
                        if compression_result.compressed_size < file_size:
                            final_data = compression_result.data
                            compression_applied = True
                            if compression_result.format:
                                content_type = compression_result.format
                                media_type = self._detect_media_type(content_type)
                except Exception as e:
                    logger.warning(f"Compression failed, using original: {e}")

            # Metadata extraction (CPU-bound)
            metadata = self._extract_metadata(
                file_data, filename, media_type, content_type, temp_path
            )

            # Collect malware scan result (ran in parallel)
            scan_status = ScanStatus.SKIPPED
            scan_result = None
            if scan_future is not None:
                try:
                    scan_status, scan_result = scan_future.result()
                    if scan_status == ScanStatus.INFECTED:
                        raise FileUploadError(
                            f"Malware detected: {scan_result}", filename
                        )
                except FileUploadError:
                    raise
                except Exception as e:
                    logger.warning(f"Scan failed: {e}")
                    scan_status = ScanStatus.ERROR
                    scan_result = str(e)

            # Storage
            stored_data_size = file_size
            checksum = checksum_raw
            storage, storage_backend = self._get_storage_for_file(
                content_type, file_size
            )
            storage_path = self._generate_storage_path(filename, media_type)

            if final_data is not None:
                storage.store(final_data, storage_path, content_type)
                stored_data_size = len(final_data)
                checksum = self._compute_checksum(final_data)
            elif file_data is not None:
                # OPTIMISATION: Use in-memory data directly
                storage.store(file_data, storage_path, content_type)
            else:
                assert temp_path is not None
                with open(temp_path, "rb") as f:
                    storage.store_stream(f, storage_path, content_type, file_size)

            file_id = self._generate_id()
            now = self._get_timestamp()
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
                    stored_data_size,
                    (media_type.value if media_type else ""),
                    storage_backend,
                    storage_path,
                    checksum,
                    user_id,
                    now,
                    metadata_json,
                    (scan_status.value if scan_status else ""),
                    scan_result,
                ),
            )

            # Background dedup registration
            if dedup_result and self._dedup_manager is not None:

                def _register_bg():
                    try:
                        self._dedup_manager.register_file(
                            hash_value=checksum,
                            file_size=stored_data_size,
                            content_type=content_type,
                            storage_path=storage_path,
                            storage_backend=storage_backend,
                            timestamp=now,
                            phash_value=None,
                        )
                    except Exception as e:
                        logger.warning(f"Background dedup registration failed: {e}")

                self._executor.submit(_register_bg)

            # Background thumbnails
            if (
                media_type == MediaType.IMAGE
                and self._image_processor
                and file_data is not None
            ):
                self._generate_thumbnails_background(file_id, file_data)

            stored_filename = os.path.basename(storage_path)
            url = f"/api/v1/media/attachments/{stored_filename}"

            compression_info = (
                f", compressed from {file_size}" if compression_applied else ""
            )
            logger.debug(
                f"File {file_id} uploaded via stream by user {user_id}: {filename} "
                f"(backend: {storage_backend}, size: {stored_data_size}"
                f"{compression_info})"
            )

            result = UploadResult(
                file_id=file_id,
                filename=filename,
                content_type=content_type,
                size=stored_data_size,
                url=url,
                thumbnails={},
                metadata=metadata if metadata else None,
                checksum=checksum,
            )

            # Fire-and-forget: rate-limit update + background pHash dedup
            self._executor.submit(self._update_rate_limit, user_id, result.size)
            if (
                self._dedup_manager
                and media_type == MediaType.IMAGE
                and file_data is not None
            ):
                self._executor.submit(
                    self._do_background_phash_dedup,
                    checksum_raw,
                    file_data,
                    content_type,
                    user_id,
                    file_size,
                )
            return result
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    # ── upload_attachment ──────────────────────────────────────────────────────

    def upload_attachment(
        self,
        user_id: int,
        file_data: bytes,
        filename: str,
        content_type: Optional[str] = None,
    ) -> AttachmentData:
        result = self.upload_file(user_id, file_data, filename, content_type)
        metadata = result.metadata or {}
        metadata["file_id"] = result.file_id
        return AttachmentData(
            filename=result.filename,
            content_type=result.content_type,
            size=result.size,
            url=result.url,
            metadata=metadata if metadata else None,
        )

    # ── metadata extraction helper ─────────────────────────────────────────────

    def _extract_metadata(
        self,
        file_data: Optional[bytes],
        filename: str,
        media_type: MediaType,
        content_type: str,
        temp_path: Optional[str] = None,
    ) -> dict:
        """Extract image/video metadata.  Returns empty dict on failure."""
        metadata: dict = {}
        if media_type == MediaType.IMAGE and self._image_processor:
            if file_data is not None:
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
                    raise FileUploadError(str(e), filename)
                except Exception as e:
                    logger.warning(f"Failed to extract image metadata: {e}")
        elif (
            media_type == MediaType.VIDEO
            and self._video_processor
            and self._video_processor.is_available()
        ):
            try:
                if file_data is not None:
                    vid_meta = self._video_processor.get_metadata_from_bytes(file_data)
                else:
                    assert temp_path is not None
                    vid_meta = self._video_processor.get_metadata(temp_path)
                metadata = {
                    "width": vid_meta.width,
                    "height": vid_meta.height,
                    "duration": vid_meta.duration,
                    "codec": vid_meta.codec,
                }
            except Exception as e:
                logger.warning(f"Failed to extract video metadata: {e}")
        return metadata
