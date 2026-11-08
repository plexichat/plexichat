"""
Avatar upload/serving validation.

Fail-closed helpers for MIME/content-type verification, magic-byte (markup)
sniffing, size limits, path-traversal guards and safe filename handling. Every
check denies by default; a validation failure raises :class:`AvatarValidationError`
which the route layer maps to a 400 without leaking exception text.
"""

from __future__ import annotations

import importlib
from typing import Any, cast

from .constants import (
    AVATAR_ALLOWED_TYPES_DEFAULT,
    AVATAR_MAX_FILE_SIZE_DEFAULT,
    is_safe_avatar_identifier,
)
from .errors import AvatarValidationError

_svg_guard = cast(Any, importlib.import_module("src.core.avatars.manager.svg_guard"))
_config_mod = cast(Any, importlib.import_module("utils.config"))

_is_unsafe_upload_payload = _svg_guard.is_unsafe_upload_payload
_is_unsafe_serve_payload = _svg_guard.is_unsafe_serve_payload
_is_blocked_avatar_content_type = _svg_guard.is_blocked_avatar_content_type


def _get_avatar_config() -> dict[str, Any]:
    """Read the operator ``avatars`` config section, failing closed to defaults.

    Config may not be set up in every context (e.g. some test fixtures), so any
    read failure falls back to the hard-coded safe defaults rather than
    degrading to a permissive/unbounded state.
    """
    try:
        section = _config_mod.get("avatars", {}) or {}
        if not isinstance(section, dict):
            return {}
        return section
    except Exception:
        return {}


def max_file_size() -> int:
    """Effective maximum upload size in bytes (config override or default)."""
    section = _get_avatar_config()
    value = section.get("max_file_size", AVATAR_MAX_FILE_SIZE_DEFAULT)
    try:
        size = int(value)
    except (TypeError, ValueError):
        return AVATAR_MAX_FILE_SIZE_DEFAULT
    return size if size > 0 else AVATAR_MAX_FILE_SIZE_DEFAULT


def allowed_types() -> tuple[str, ...]:
    """Effective allowed raster media types (config override or default)."""
    section = _get_avatar_config()
    value = section.get("allowed_types", AVATAR_ALLOWED_TYPES_DEFAULT)
    if isinstance(value, (list, tuple)):
        types = tuple(str(t).lower() for t in value)
        if types:
            return types
    return AVATAR_ALLOWED_TYPES_DEFAULT


def validate_upload_magic_bytes(content_type: str | None, data: bytes) -> None:
    """Reject an upload whose type or bytes are unsafe.

    Raises :class:`AvatarValidationError` on the first failing check. Fail
    closed: if the payload cannot be inspected, it is rejected.
    """
    try:
        unsafe = _is_unsafe_upload_payload(content_type, data)
    except Exception:
        unsafe = True
    if unsafe:
        raise AvatarValidationError("File must be an image")


def validate_upload_size(data: bytes) -> None:
    """Reject an upload that exceeds the configured maximum size."""
    if len(data) > max_file_size():
        raise AvatarValidationError("File too large")


def validate_avatar_upload(content_type: str | None, data: bytes) -> None:
    """Full upload validation: media type, magic bytes, size.

    Intended to be called after ``file.read()`` so the full payload is
    available for magic-byte sniffing.
    """
    if not content_type or not str(content_type).startswith("image/"):
        raise AvatarValidationError("File must be an image")
    normalized = str(content_type).split(";")[0].strip().lower()
    if normalized not in allowed_types():
        raise AvatarValidationError("File must be an image")
    validate_upload_magic_bytes(content_type, data)
    validate_upload_size(data)


def is_serve_safe(content_type: str | None, data: bytes) -> bool:
    """True when a stored avatar/icon payload may be served raw.

    Fail closed: blocked content types and any payload carrying active markup
    are refused; inspection failures default to unsafe.
    """
    if _is_blocked_avatar_content_type(content_type):
        return False
    try:
        return not _is_unsafe_serve_payload(content_type, data)
    except Exception:
        return False


def parse_server_id(server_id: str) -> int:
    """Parse and validate a server id path parameter.

    Rejects non-integers, negatives and path-traversal markers so server id
    values can never escape their intended namespace.
    """
    try:
        sid = int(server_id)
    except (ValueError, TypeError):
        raise AvatarValidationError("Invalid server ID")
    if sid < 0:
        raise AvatarValidationError("Invalid server ID")
    if not is_safe_avatar_identifier(str(sid)):
        raise AvatarValidationError("Invalid server ID")
    return sid


def sanitize_filename(filename: str | None) -> str:
    """Return a safe, traversal-free filename stem.

    Strips directory separators and traversal markers; never returns an empty
    stem (falls back to ``unnamed``). No secret material is logged.
    """
    if not filename:
        return "unnamed"
    stem = str(filename)
    for sep in ("/", "\\", "\x00"):
        stem = stem.replace(sep, "_")
    stem = stem.strip(" .")
    if ".." in stem or not stem:
        return "unnamed"
    return stem[:255]


__all__ = [
    "validate_avatar_upload",
    "validate_upload_magic_bytes",
    "validate_upload_size",
    "is_serve_safe",
    "parse_server_id",
    "sanitize_filename",
    "max_file_size",
    "allowed_types",
    "_get_avatar_config",
]
