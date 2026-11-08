"""Typed exceptions for avatar route operations.

All avatar route failures derive from :class:`AvatarError` so callers can
catch the whole family. Client-actionable problems (bad file, missing
permission, not found) are distinguished from internal storage failures so the
HTTP layer can map them to honest status codes without leaking exception text.
"""

from __future__ import annotations


class AvatarError(RuntimeError):
    """Base exception for avatar route operations."""


class AvatarValidationError(AvatarError):
    """Raised when an uploaded avatar/icon fails validation (client error)."""


class AvatarNotFoundError(AvatarError):
    """Raised when the requested avatar/icon does not exist."""


class AvatarStorageError(AvatarError):
    """Raised when the underlying avatar store/manager fails (server error)."""


class AvatarUnauthorizedError(AvatarError):
    """Raised when the caller lacks identity for the requested avatar."""


class AvatarForbiddenError(AvatarError):
    """Raised when the caller is authenticated but not permitted."""


__all__ = [
    "AvatarError",
    "AvatarValidationError",
    "AvatarNotFoundError",
    "AvatarStorageError",
    "AvatarUnauthorizedError",
    "AvatarForbiddenError",
]
