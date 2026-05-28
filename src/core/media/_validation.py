# pyright: reportAttributeAccessIssue=false
"""
Media validation utilities (content-type detection, magic bytes, filename sanitization).

These are methods of MediaManager – kept in a mixin class for clean separation.
"""

import os
import logging

from .models import MediaType
from .exceptions import FileTypeError, FileSizeError
from .security.validation import BLOCKED_MIME_TYPES

logger = logging.getLogger(__name__)


class _ValidationMixin:
    """Validation helpers mixed into MediaManager."""

    # -- filename ----------------------------------------------------------------

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename: remove traversal, null bytes, control chars, cap length."""
        filename = filename.replace("\\", "/")
        filename = os.path.basename(filename)
        filename = filename.replace("..", "")
        filename = "".join(c for c in filename if ord(c) >= 32 and ord(c) != 127)
        if len(filename) > 250:
            name, ext = os.path.splitext(filename)
            max_name_len = 250 - len(ext)
            filename = name[:max_name_len] + ext
        if not filename or filename.strip() == ".":
            filename = f"unnamed_file_{self._get_timestamp() // 1000}"
        return filename

    # -- content-type detection --------------------------------------------------

    def _detect_content_type(self, file_data: bytes, fallback: str) -> str:
        """Detect actual content type from magic bytes."""
        signatures = {
            b"\xff\xd8\xff": "image/jpeg",
            b"\x89PNG\r\n\x1a\n": "image/png",
            b"GIF87a": "image/gif",
            b"GIF89a": "image/gif",
            b"RIFF": "image/webp",
            b"%PDF": "application/pdf",
        }
        for sig, mime in signatures.items():
            if file_data.startswith(sig):
                return mime
        return fallback

    def _detect_media_type(self, content_type: str) -> MediaType:
        """Map content-type string to MediaType enum."""
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

    # -- magic-byte validation ---------------------------------------------------

    def _validate_magic_bytes(self, file_data: bytes, content_type: str) -> bool:
        """Validate file content matches declared content type using magic bytes."""
        magic_signatures = {
            "image/jpeg": [b"\xff\xd8\xff"],
            "image/png": [b"\x89PNG\r\n\x1a\n"],
            "image/gif": [b"GIF87a", b"GIF89a"],
            "image/webp": [b"RIFF"],
            "image/bmp": [b"BM"],
            "image/tiff": [b"II*\x00", b"MM\x00*"],
            "video/mp4": [
                b"\x00\x00\x00\x18ftypmp4",
                b"\x00\x00\x00\x1cftypmp4",
                b"\x00\x00\x00 ftypisom",
                b"ftyp",
            ],
            "video/webm": [b"\x1a\x45\xdf\xa3"],
            "video/quicktime": [b"\x00\x00\x00\x14ftypqt", b"ftypqt"],
            "audio/mpeg": [b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2", b"ID3"],
            "audio/ogg": [b"OggS"],
            "audio/wav": [b"RIFF"],
            "audio/webm": [b"\x1a\x45\xdf\xa3"],
            "application/pdf": [b"%PDF"],
            "application/zip": [b"PK\x03\x04", b"PK\x05\x06"],
            "text/plain": [],
            "text/markdown": [],
            "text/csv": [],
            "application/json": [],
        }
        ct_lower = content_type.lower()
        if ct_lower not in magic_signatures:
            return True
        signatures = magic_signatures[ct_lower]
        if not signatures:
            return True
        shortest_sig_len = min(len(s) for s in signatures)
        if len(file_data) < shortest_sig_len:
            return False
        for sig in signatures:
            if file_data.startswith(sig):
                return True
            if (
                ct_lower in ("video/mp4", "video/quicktime")
                and len(file_data) >= 12
                and b"ftyp" in file_data[:12]
            ):
                return True
        return False

    # -- content-type whitelist --------------------------------------------------

    def _validate_content_type(self, content_type: str, media_type: MediaType):
        """Validate content type is allowed."""
        from ._config import DEFAULT_ALLOWED_TYPES

        allowed = self._config.get("allowed_types", DEFAULT_ALLOWED_TYPES)
        type_key = media_type.value
        ct_lower = content_type.lower()

        if ct_lower in BLOCKED_MIME_TYPES:
            raise FileTypeError(
                f"File type '{content_type}' is blocked for security reasons.",
                content_type,
                ["Contact an administrator for more information"],
            )
        if type_key in allowed:
            if ct_lower not in allowed[type_key]:
                raise FileTypeError(
                    f"Content type '{content_type}' is not allowed for {type_key} uploads.",
                    content_type,
                    allowed[type_key],
                )
        elif type_key == "other":
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

    # -- file-size validation ----------------------------------------------------

    def _validate_file_size(self, size: int, media_type: MediaType):
        """Validate file size is within limits."""
        from ._config import DEFAULT_SIZE_LIMITS

        limits = self._config.get("size_limits", DEFAULT_SIZE_LIMITS)
        type_key = media_type.value
        max_size = limits.get(type_key, limits.get("other", 10 * 1024 * 1024))
        if size > max_size:
            raise FileSizeError(
                f"File size {size} exceeds limit {max_size}",
                max_size,
                size,
            )
