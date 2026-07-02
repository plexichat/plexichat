"""
Media security validation - Magic byte validation and executable blocking.

Provides security checks for uploaded files including:
- Magic byte validation to prevent MIME type spoofing
- Executable file blocking
- Path traversal prevention
- Filename sanitization
- XML/SVG sanitization via defusedxml + element allow-list
"""

# SECURITY: ``defusedxml`` is a hard runtime dependency for the
# SVG/proper-XML sanitization in :class:`FileValidator`. Plexichat
# refuses to import this module without it, because falling back to
# stdlib ``xml.etree`` would re-enable XXE / billion-laughs
# processing of attacker-controlled SVG payloads. This module-load
# guard runs for every entry point that imports this validation
# module (web server via ``app.py``, the CLI, migration tools, and
# admin-tools), not just those that go through the FastAPI factory.
try:
    import defusedxml  # noqa: F401
except ImportError:  # pragma: no cover
    raise ImportError(
        "defusedxml is required for safe SVG/XML handling in plexichat. "
        "Install it with: pip install defusedxml"
    ) from None

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
    "image/svg+xml": [(0, b"<svg"), (0, b"<?xml")],  # SVG/XML magic bytes
    # Videos
    "video/mp4": [(4, b"ftyp")],  # ftyp at offset 4
    "video/webm": [(0, b"\x1a\x45\xdf\xa3")],
    "video/quicktime": [(4, b"ftyp")],
    "video/x-msvideo": [(0, b"RIFF"), (8, b"AVI ")],
    "video/x-matroska": [(0, b"\x1a\x45\xdf\xa3")],
    # Audio
    "audio/mpeg": [
        (0, b"\xff\xfb"),
        (0, b"\xff\xfa"),
        (0, b"\xff\xf3"),
        (0, b"\xff\xf2"),
        (0, b"ID3"),
    ],
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
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [
        (0, b"PK\x03\x04")
    ],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [
        (0, b"PK\x03\x04")
    ],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [
        (0, b"PK\x03\x04")
    ],
    # Web formats
    "text/html": [(0, b"<html"), (0, b"<!DOCTYPE html")],
    "application/xml": [(0, b"<?xml")],
    # Text types - no magic bytes
    "text/plain": [],
    "text/markdown": [],
    "text/csv": [],
    "application/json": [],
}

# Dangerous executable extensions
BLOCKED_EXTENSIONS = {
    # Windows executables
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".msi",
    ".msp",
    ".msc",
    ".pif",
    ".scr",
    ".hta",
    ".cpl",
    ".msu",
    # Windows scripts
    ".ps1",
    ".psm1",
    ".psd1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".ws",
    ".wsf",
    ".wsc",
    ".wsh",
    # Unix executables
    ".sh",
    ".bash",
    ".zsh",
    ".csh",
    ".ksh",
    ".run",
    ".bin",
    ".elf",
    # Libraries
    ".dll",
    ".so",
    ".dylib",
    # Java
    ".jar",
    ".class",
    # Python (can be dangerous)
    ".py",
    ".pyc",
    ".pyo",
    ".pyw",
    # Other
    ".app",
    ".deb",
    ".rpm",
    ".dmg",
    ".pkg",
    ".reg",
    ".inf",
    ".lnk",
    ".url",
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
            "blocked_extensions": set(
                security_config.get("blocked_extensions", BLOCKED_EXTENSIONS)
            ),
            "blocked_mime_types": set(
                security_config.get("blocked_mime_types", BLOCKED_MIME_TYPES)
            ),
        }

    def validate_magic_bytes(
        self, file_data: bytes, content_type: str
    ) -> Tuple[bool, Optional[str]]:
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

        # Check if file matches any valid signature (case-insensitive for text-based formats)
        file_data_lower = file_data.lower()
        for offset, sig in signatures:
            if len(file_data) >= offset + len(sig):
                if file_data_lower[offset : offset + len(sig)] == sig.lower():
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

    def is_executable_blocked(
        self, filename: str, content_type: str
    ) -> Tuple[bool, Optional[str]]:
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
        import posixpath

        # Normalize path separators first (handle both Unix and Windows paths)
        filename = filename.replace("\\", "/")

        # Extract just the filename part (remove all path components)
        filename = posixpath.basename(filename)

        # Remove null bytes
        filename = filename.replace("\x00", "")

        # Remove path traversal attempts - repeatedly replace until no more
        while ".." in filename:
            filename = filename.replace("..", "")

        # Remove control characters
        filename = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", filename)

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
        self, file_data: bytes, filename: str, content_type: str
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

        # SECURITY: SVG XSS sanitization via real XML parsing + an
        # explicit element/attribute allow-list. The previous
        # implementation did a regex substring scan against an
        # attacker-supplied byte stream (``<script``, ``onclick``,
        # ``javascript:``); that scan is trivially bypassable via
        # CDATA sections, XML entities, mixed-case tags, NULL-byte
        # insertion, ``xlink:href`` usage in ``<use>`` elements, or
        # any of the standard SMIL/foreignObject vectors. We now:
        #   1) parse the SVG as XML using ``defusedxml`` (which
        #      disables DTD/entity-resolution primitives),
        #   2) walk the parsed tree and reject any node whose
        #      element name is NOT in the structural allow-list,
        #   3) reject any attribute starting with ``on`` (event
        #      handlers) OR whose key matches a known-dangerous
        #      href binding (``href``, ``xlink:href``,
        #      ``src``) AND whose resolved URI scheme is not in the
        #      safe-scheme list,
        #   4) fail CLOSED on any parse error (defusedxml raises on
        #      XXE/billion-laughs by design).
        if content_type.lower() == "image/svg+xml":
            is_safe, reason = self._sanitize_svg(file_data)
            if not is_safe:
                logger.warning(f"Unsafe SVG blocked: {reason}")
                return False, "Unsafe content detected in SVG file", safe_filename

        return True, None, safe_filename

    @staticmethod
    def _sanitize_svg(file_data: bytes) -> tuple:
        """Parse + allow-list SVG ``file_data``. Returns (ok, reason)."""
        # Allow-list of structural SVG elements. Anything outside
        # this set is refused (script, foreignObject, iframe, etc.).
        # Note: <style> is allowed but its content is sanitized for CSS-based XSS.
        _ALLOWED_ELEMENTS = frozenset(
            {
                "svg",
                "g",
                "defs",
                "symbol",
                "use",
                "title",
                "desc",
                "metadata",
                "style",
                "rect",
                "circle",
                "ellipse",
                "line",
                "polyline",
                "polygon",
                "path",
                "text",
                "tspan",
                "textPath",
                "marker",
                "mask",
                "clipPath",
                "linearGradient",
                "radialGradient",
                "stop",
                "pattern",
                "image",
                "view",
                "switch",
                "animate",
                "animateTransform",
                "animateMotion",
                "set",
                "mpath",
                "feBlend",
                "feColorMatrix",
                "feComponentTransfer",
                "feComposite",
                "feConvolveMatrix",
                "feDiffuseLighting",
                "feDisplacementMap",
                "feDistantLight",
                "feFlood",
                "feFuncA",
                "feFuncB",
                "feFuncG",
                "feFuncR",
                "feGaussianBlur",
                "feImage",
                "feMerge",
                "feMergeNode",
                "feMorphology",
                "feOffset",
                "fePointLight",
                "feSpecularLighting",
                "feSpotLight",
                "feTile",
                "feTurbulence",
            }
        )

        # Attribute keys that may legitimately carry a URI on a safe
        # element. Each one is also subject to a scheme allow-list:
        # we never let attackers inject javascript:, vbscript:, data:
        # text/html, file:, ftp:, gopher:, ws:, wss:, etc.
        # Only http:, https:, and # (fragment) are allowed.
        _URI_ATTRS = frozenset({"href", "xlink:href", "src", "action", "formaction"})
        _SAFE_URI_SCHEMES = frozenset({"", "#", "http", "https"})
        _SAFE_DATA_PREFIXES = (
            "data:image/png;",
            "data:image/jpeg;",
            "data:image/gif;",
            "data:image/webp;",
            "data:image/svg+xml;",
        )

        def _check_uri(value: str) -> bool:
            v = value.strip().lower()
            if not v:
                return True
            for prefix in _SAFE_DATA_PREFIXES:
                if v.startswith(prefix):
                    return True
            # Strip and inspect the scheme; reject javascript:, vbscript:,
            # data:text/html, file:, ftp:, gopher:, ws:, wss:, etc.
            if ":" in v:
                scheme = v.split(":", 1)[0]
                if scheme not in {"http", "https"} and not v.startswith("#"):
                    return False
            return True

        def _sanitize_css(css_content: str) -> Tuple[bool, str]:
            """
            Sanitize CSS content to prevent CSS-based XSS attacks.

            Blocks dangerous patterns like:
            - url(javascript:...)
            - url(vbscript:...)
            - url(data:text/html,...)
            - url(file:...)
            - url(ftp:...)
            - url(gopher:...)
            - url(ws:...)
            - url(wss:...)
            """
            import re

            # Pattern to find url() function calls
            url_pattern = re.compile(
                r'url\s*\(\s*[\'"]?([^\'"]*)[\'"]?\s*\)', re.IGNORECASE
            )

            for match in url_pattern.finditer(css_content):
                url_value = match.group(1).strip()

                # First, check if it's a safe data URI (data:image/...)
                if url_value.startswith("data:"):
                    # Only allow data: URIs for safe image types
                    safe_data_prefixes = (
                        "data:image/png;",
                        "data:image/jpeg;",
                        "data:image/gif;",
                        "data:image/webp;",
                        "data:image/svg+xml;",
                    )
                    if not any(
                        url_value.startswith(prefix) for prefix in safe_data_prefixes
                    ):
                        return (
                            False,
                            f"unsafe_data_uri:{url_value} detected in style element",
                        )
                    continue  # Safe data URI, continue to next check

                # Check for dangerous schemes in url()
                dangerous_schemes = {
                    "javascript:",
                    "vbscript:",
                    "file:",
                    "ftp:",
                    "gopher:",
                    "ws:",
                    "wss:",
                    "data:text/html:",
                    "data:text/javascript:",
                    "data:application/javascript:",
                }

                for scheme in dangerous_schemes:
                    if url_value.startswith(scheme):
                        return (
                            False,
                            f"dangerous_css_url:{scheme} detected in style element",
                        )

                # Check for external references that could be used for tracking
                # Block any external domain references in url()
                if url_value and not url_value.startswith("#"):
                    # Block any external HTTP/HTTPS references in style elements
                    # to prevent tracking and exfiltration
                    return (
                        False,
                        f"external_reference:{url_value} detected in style element",
                    )

            return True, ""

        try:
            try:
                from defusedxml.ElementTree import fromstring as _dx_fromstring
            except ImportError:
                # SECURITY: defusedxml is required for safe SVG
                # parsing. We deliberately do NOT fall back to stdlib
                # ElementTree because stdlib honours external
                # entities / billion-laughs by default. Refuse the
                # file outright when defusedxml is missing rather
                # than risk a fallback that re-introduces XXE.
                logger.critical(
                    "defusedxml is not installed; refusing SVG file "
                    "rather than risk XXE / entity-expansion XSS."
                )
                return False, "defusedxml_unavailable"

            # Cap decode to a sane size so a multi-MiB SVG cannot
            # exhaust worker memory during sanitation.
            try:
                text = file_data[: 4 * 1024 * 1024].decode("utf-8", errors="replace")
            except Exception:
                return False, "decode_failed"
            try:
                root = _dx_fromstring(text)
            except Exception as parse_exc:
                # defusedxml raises ParseError on entity expansion
                # / XXE — that's a HIGH finding and must NOT silently
                # pass as "the SVG is fine".
                return False, f"xml_parse_failed:{type(parse_exc).__name__}"

            # Walk the tree. We refuse on any element that is
            # outside the structural allow-list AND on any
            # attribute that is event-handler (``on*``) or that
            # carries a non-allow-listed URI scheme.
            stack = [root]
            while stack:
                elem = stack.pop()
                tag = elem.tag
                # ElementTree tags are ``{namespace}localname``.
                if isinstance(tag, str) and tag.startswith("{"):
                    local = tag.split("}", 1)[1].lower()
                else:
                    local = (tag or "").lower()
                if local not in _ALLOWED_ELEMENTS:
                    return (
                        False,
                        f"disallowed_element:{local!r}",
                    )
                try:
                    attr_dict = elem.attrib or {}
                except Exception:
                    attr_dict = {}
                for attr_key, attr_val in attr_dict.items():
                    lower_key = attr_key.lower()
                    # ElementTree namespaced attributes come back as
                    # ``{ns}localname`` -- normalise to ``local``.
                    if lower_key.startswith("{") and "}" in lower_key:
                        lower_key = lower_key.split("}", 1)[1]
                    if lower_key.startswith("on"):
                        return False, f"event_handler_attr:{lower_key!r}"
                    if lower_key in _URI_ATTRS and isinstance(attr_val, str):
                        if not _check_uri(attr_val):
                            return (
                                False,
                                f"unsafe_uri:{lower_key}={attr_val!r}",
                            )
                # Special handling for style elements to sanitize CSS content
                if local == "style" and elem.text:
                    css_content = elem.text.strip()
                    if css_content:
                        is_safe, css_reason = _sanitize_css(css_content)
                        if not is_safe:
                            return False, css_reason
                # Special handling for use, image, and feImage elements
                # to enforce stricter URI validation
                if local in ("use", "image", "feimage"):
                    # For these elements, only allow local references (#) or
                    # trusted external domains if explicitly configured
                    for attr_key, attr_val in attr_dict.items():
                        lower_key = attr_key.lower()
                        if lower_key.startswith("{") and "}" in lower_key:
                            lower_key = lower_key.split("}", 1)[1]
                        if lower_key in ("href", "xlink:href") and isinstance(
                            attr_val, str
                        ):
                            v = attr_val.strip().lower()
                            if v and not v.startswith("#"):
                                # Block all external references for use, image, and feImage
                                # to prevent SVG injection and tracking
                                return (
                                    False,
                                    f"external_reference:{lower_key}={v!r}",
                                )
                # Walk children without iterating the live tree
                # during mutation.
                try:
                    stack.extend(list(elem))
                except Exception:
                    pass
            return True, ""
        except Exception as exc:
            # Catch-all: any unexpected failure must FAIL CLOSED.
            logger.error(f"SVG sanitation unexpected error: {exc}")
            return False, "sanitize_error"


# Singleton instance
_validator: Optional[FileValidator] = None


def get_validator() -> FileValidator:
    """Get or create the file validator instance."""
    global _validator
    if _validator is None:
        _validator = FileValidator()
    return _validator


def validate_file(
    file_data: bytes, filename: str, content_type: str
) -> Tuple[bool, Optional[str], str]:
    """Convenience function to validate a file."""
    return get_validator().validate_file(file_data, filename, content_type)


def sanitize_filename(filename: str) -> str:
    """Convenience function to sanitize a filename."""
    return get_validator().sanitize_filename(filename)
