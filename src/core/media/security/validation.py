"""
Media security validation - Magic byte validation and executable blocking.

Provides security checks for uploaded files including:
- Magic byte validation to prevent MIME type spoofing
- Executable file blocking
- Path traversal prevention
- Filename sanitization
"""

import os
import re
from typing import Optional, Tuple

import utils.logger as logger
import utils.config as config


# Magic byte signatures for common file types
MAGIC_SIGNATURES = {
    # Images
    "image/jpeg": [(0, b"\xff\xd8\xff")],
    "image/png": [(0, b"\x89PNG\r\n\x1a\n")],
    "image/gif": [(0, b"GIF87a"), (0, b"GIF89a")],
    "image/webp": [(0, b"RIFF"), (8, b"WEBP")],  # RIFF....WEBP
    "image/bmp": [(0, b"BM")],
    "image/tiff": [(0, b"II*\x00"), (0, b"MM\x00*")],
    "image/x-icon": [(0, b"\x00\x00\x01\x00"), (0, b"\x00\x00\x02\x00")],
    "image/svg+xml": [],  # Text-based, no magic bytes

    # Videos
    "video/mp4": [(4, b"ftyp")],  # ftyp at offset 4
    "video/webm": [(0, b"\x1a\x45\xdf\xa3")],
    "video/quicktime": [(4, b"ftyp")],
    "video/x-msvideo": [(0, b"RIFF"), (8, b"AVI ")],
    "video/x-matroska": [(0, b"\x1a\x45\xdf\xa3")],

    # Audio
    "audio/mpeg": [(0, b"\xff\xfb"), (0, b"\xff\xfa"), (0, b"\xff\xf3"), (0, b"\xff\xf2"), (0, b"ID3")],
    "audio/ogg": [(0, b"OggS")],
    "audio/wav": [(0, b"RIFF"), (8, b"WAVE")],
    "audio/webm": [(0, b"\x1a\x45\xdf\xa3")],
    "audio/flac": [(0, b"fLaC")],
    "audio/aac": [(0, b"\xff\xf1"), (0, b"\xff\xf9")],

    # Documents
    "application/pdf": [(0, b"%PDF")],
    "application/zip": [(0, b"PK\x03\x04"), (0, b"PK\x05\x06"), (0, b"PK\x07\x08")],
    "application/x-rar-compressed": [(0, b"Rar!\x1a\x07")],
    "application/x-7z-compressed": [(0, b"7z\xbc\xaf\x27\x1c")],
    "application/gzip": [(0, b"\x1f\x8b")],

    # Office documents (ZIP-based)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [(0, b"PK\x03\x04")],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [(0, b"PK\x03\x04")],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [(0, b"PK\x03\x04")],

    # Text types - no magic bytes
    "text/plain": [],
    "text/markdown": [],
    "text/csv": [],
    "text/html": [],
    "application/json": [],
    "application/xml": [],
}

# Dangerous executable extensions
BLOCKED_EXTENSIONS = {
    # Windows executables
    ".exe", ".bat", ".cmd", ".com", ".msi", ".msp", ".msc",
    ".pif", ".scr", ".hta", ".cpl", ".msu",
    # Windows scripts
    ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse",
    ".ws", ".wsf", ".wsc", ".wsh",
    # Unix executables
    ".sh", ".bash", ".zsh", ".csh", ".ksh",
    ".run", ".bin", ".elf",
    # Libraries
    ".dll", ".so", ".dylib",
    # Java
    ".jar", ".class",
    # Python (can be dangerous)
    ".py", ".pyc", ".pyo", ".pyw",
    # Other
    ".app", ".deb", ".rpm", ".dmg", ".pkg",
    ".reg", ".inf", ".lnk", ".url",
}

# Dangerous MIME types
BLOCKED_MIME_TYPES = {
    "application/x-executable",
    "application/x-msdos-program",
    "application/x-msdownload",
    "application/x-dosexec",
    "application/x-sh",
    "application/x-shellscript",
    "application/x-bat",
    "application/x-msi",
    "application/vnd.microsoft.portable-executable",
    "application/x-java-archive",
    "application/java-archive",
}


class FileValidator:
    """Validates uploaded files for security."""

    def __init__(self):
        """Initialize validator with config."""
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load security configuration."""
        media_config = config.get("media", {})
        security_config = media_config.get("security", {})

        return {
            "magic_byte_validation": security_config.get("magic_byte_validation", True),
            "block_executables": security_config.get("block_executables", True),
            "blocked_extensions": set(security_config.get("blocked_extensions", BLOCKED_EXTENSIONS)),
            "blocked_mime_types": set(security_config.get("blocked_mime_types", BLOCKED_MIME_TYPES)),
        }

    def validate_magic_bytes(self, file_data: bytes, content_type: str) -> Tuple[bool, Optional[str]]:
        """
        Validate file content matches declared content type using magic bytes.
        
        Args:
            file_data: Raw file bytes
            content_type: Declared content type
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self._config["magic_byte_validation"]:
            return True, None

        if len(file_data) < 16:
            return True, None  # Too small to validate

        ct_lower = content_type.lower()

        # If we don't have signatures for this type, allow through
        if ct_lower not in MAGIC_SIGNATURES:
            return True, None

        signatures = MAGIC_SIGNATURES[ct_lower]

        # Text types have no magic bytes
        if not signatures:
            return True, None

        # Check if file matches any valid signature
        for offset, sig in signatures:
            if len(file_data) >= offset + len(sig):
                if file_data[offset:offset + len(sig)] == sig:
                    return True, None

        # Special handling for container formats
        if ct_lower in ("video/mp4", "video/quicktime"):
            # ftyp can be at various offsets
            if b"ftyp" in file_data[:32]:
                return True, None

        if ct_lower in ("image/webp", "audio/wav", "video/x-msvideo"):
            # RIFF-based formats
            if file_data[:4] == b"RIFF":
                return True, None

        logger.warning(f"Magic byte validation failed for content type: {content_type}")
        return False, f"File content does not match declared type: {content_type}"

    def is_executable_blocked(self, filename: str, content_type: str) -> Tuple[bool, Optional[str]]:
        """
        Check if file is a blocked executable type.
        
        Args:
            filename: Original filename
            content_type: MIME type
            
        Returns:
            Tuple of (is_blocked, error_message)
        """
        if not self._config["block_executables"]:
            return False, None

        # Check extension
        ext = os.path.splitext(filename.lower())[1]
        if ext in self._config["blocked_extensions"]:
            logger.warning(f"Blocked executable extension: {ext}")
            return True, f"File type not allowed: {ext}"

        # Check MIME type
        if content_type.lower() in self._config["blocked_mime_types"]:
            logger.warning(f"Blocked executable MIME type: {content_type}")
            return True, f"Content type not allowed: {content_type}"

        return False, None

    def sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename to prevent path traversal and other attacks.
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename
        """
        # Normalize path separators first (handle both Unix and Windows paths)
        filename = filename.replace("\\", "/")
        
        # Remove path components
        filename = os.path.basename(filename)

        # Remove null bytes
        filename = filename.replace("\x00", "")

        # Remove path traversal attempts
        filename = filename.replace("..", "")
        filename = filename.replace("/", "_")
        filename = filename.replace("\\", "_")

        # Remove control characters
        filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', filename)

        # Limit length
        name, ext = os.path.splitext(filename)
        if len(name) > 200:
            name = name[:200]
        if len(ext) > 20:
            ext = ext[:20]

        filename = name + ext

        # Ensure not empty
        if not filename or filename.strip() == "":
            filename = "unnamed_file"

        return filename

    def validate_file(
        self,
        file_data: bytes,
        filename: str,
        content_type: str
    ) -> Tuple[bool, Optional[str], str]:
        """
        Perform all security validations on a file.
        
        Args:
            file_data: Raw file bytes
            filename: Original filename
            content_type: MIME type
            
        Returns:
            Tuple of (is_valid, error_message, sanitized_filename)
        """
        # Sanitize filename first
        safe_filename = self.sanitize_filename(filename)

        # Check for blocked executables
        is_blocked, error = self.is_executable_blocked(safe_filename, content_type)
        if is_blocked:
            return False, error, safe_filename

        # Validate magic bytes
        is_valid, error = self.validate_magic_bytes(file_data, content_type)
        if not is_valid:
            return False, error, safe_filename

        return True, None, safe_filename


# Singleton instance
_validator: Optional[FileValidator] = None


def get_validator() -> FileValidator:
    """Get or create the file validator instance."""
    global _validator
    if _validator is None:
        _validator = FileValidator()
    return _validator


def validate_file(file_data: bytes, filename: str, content_type: str) -> Tuple[bool, Optional[str], str]:
    """Convenience function to validate a file."""
    return get_validator().validate_file(file_data, filename, content_type)


def sanitize_filename(filename: str) -> str:
    """Convenience function to sanitize a filename."""
    return get_validator().sanitize_filename(filename)
