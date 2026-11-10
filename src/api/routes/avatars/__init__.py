"""
Avatar routes - Endpoints for user avatars and server icons.

This package is a drop-in replacement for the former monolithic
``src.api.routes.avatars`` module. It keeps the full public surface (and every
"name" existing tests and callers import, including private cache/invalidation
helpers) available directly from the package namespace while delegating
single-responsibility work to focused submodules:

- :mod:`errors`        - typed exceptions
- :mod:`constants`     - size limits, allowed MIME types, path prefixes
- :mod:`models`        - typed dataclasses for avatar metadata
- :mod:`validation`    - file type/size/content validation, path-traversal guards
- :mod:`storage`       - avatar save/delete/read logic + bounded checksum caches
- :mod:`routes`        - HTTP route handlers (authz, upload, serve, delete)

Importing this package (``from src.api.routes import avatars`` or
``from src.api.routes.avatars import upload_my_avatar``) behaves identically to
the old module.
"""

from __future__ import annotations

import importlib
from typing import Any, cast

# Submodules are imported so they remain accessible as package attributes
# (e.g. ``avatars.validation``, ``avatars.storage``) and their public names are
# re-exported below for backward compatibility.
from . import constants, errors, models, storage, validation  # noqa: F401
from .routes import (  # noqa: F401
    router,
    get_user_avatar,
    upload_my_avatar,
    delete_my_avatar,
    get_server_icon,
    upload_server_icon,
    delete_server_icon,
)
from .storage import (  # noqa: F401
    _BoundedChecksumCache,
    _cache_set_checksum,
    _avatar_checksum_cache,
    _icon_checksum_cache,
    _invalidate_user_avatar_cache,
    _invalidate_server_icon_cache,
)
from .constants import (  # noqa: F401
    AVATAR_MAX_FILE_SIZE_DEFAULT,
    AVATAR_MAX_DIMENSION_DEFAULT,
    AVATAR_MAX_PIXELS_DEFAULT,
    AVATAR_ALLOWED_TYPES_DEFAULT,
    AVATAR_DEFAULT_COLORS_DEFAULT,
    USER_AVATAR_URL_PREFIX,
    SERVER_ICON_URL_PREFIX,
    _MAX_CHECKSUM_CACHE_ENTRIES,
    _CHECKSUM_CACHE_TTL_SEC,
    is_safe_avatar_identifier,
)
from .validation import (  # noqa: F401
    validate_avatar_upload,
    validate_upload_magic_bytes,
    validate_upload_size,
    is_serve_safe,
    parse_server_id,
    sanitize_filename,
    max_file_size,
    allowed_types,
)
from .errors import (  # noqa: F401
    AvatarError,
    AvatarValidationError,
    AvatarNotFoundError,
    AvatarStorageError,
    AvatarUnauthorizedError,
    AvatarForbiddenError,
)
from .models import (  # noqa: F401
    AvatarMetadata,
    AvatarUploadResult,
    metadata_from_manager,
    upload_result_from_manager,
)

# Optional-dependency shim kept for compatibility with code that reached for
# ``avatars.importlib`` after the vault-style split.
_importlib = cast(Any, importlib.import_module)


__all__ = [
    # Submodules
    "constants",
    "errors",
    "models",
    "storage",
    "validation",
    "routes",
    # Router + handlers
    "router",
    "get_user_avatar",
    "upload_my_avatar",
    "delete_my_avatar",
    "get_server_icon",
    "upload_server_icon",
    "delete_server_icon",
    # Cache (M5) - public surface + private names tests patch/monkeypatch
    "_BoundedChecksumCache",
    "_cache_set_checksum",
    "_avatar_checksum_cache",
    "_icon_checksum_cache",
    "_MAX_CHECKSUM_CACHE_ENTRIES",
    "_CHECKSUM_CACHE_TTL_SEC",
    # Cache invalidation helpers (private names re-used across the repo)
    "_invalidate_user_avatar_cache",
    "_invalidate_server_icon_cache",
    # Security / validation helpers
    "validate_avatar_upload",
    "validate_upload_magic_bytes",
    "validate_upload_size",
    "is_serve_safe",
    "parse_server_id",
    "sanitize_filename",
    "max_file_size",
    "allowed_types",
    "is_safe_avatar_identifier",
    # Config defaults
    "AVATAR_MAX_FILE_SIZE_DEFAULT",
    "AVATAR_MAX_DIMENSION_DEFAULT",
    "AVATAR_MAX_PIXELS_DEFAULT",
    "AVATAR_ALLOWED_TYPES_DEFAULT",
    "AVATAR_DEFAULT_COLORS_DEFAULT",
    "USER_AVATAR_URL_PREFIX",
    "SERVER_ICON_URL_PREFIX",
    # Typed errors
    "AvatarError",
    "AvatarValidationError",
    "AvatarNotFoundError",
    "AvatarStorageError",
    "AvatarUnauthorizedError",
    "AvatarForbiddenError",
    # Typed models
    "AvatarMetadata",
    "AvatarUploadResult",
    "metadata_from_manager",
    "upload_result_from_manager",
    # Compat shim
    "importlib",
]
