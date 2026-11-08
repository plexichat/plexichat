"""
Avatar route constants and defaults.

Central, single-source-of-truth values for avatar/upload limits, allowed media
types, URL prefixes and cache tuning. Operators may override the ``avatars``
config section (see ``src/config_defaults.py``); this module falls back to
hard-coded safe defaults when config is unavailable so validation never
silently degrades to an unbounded/permissive state.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Size / dimension limits (bytes, pixels). Mirror the operator config defaults
# in src/config_defaults.py so route-level validation agrees with the manager.
# ---------------------------------------------------------------------------

AVATAR_MAX_FILE_SIZE_DEFAULT: int = 5 * 1024 * 1024  # 5 MiB
AVATAR_MAX_DIMENSION_DEFAULT: int = 16384
AVATAR_MAX_PIXELS_DEFAULT: int = 178_956_970

# ---------------------------------------------------------------------------
# Allowed media types. SVG/HTML/JS are never allowed here regardless of config
# (the svg_guard block list is authoritative for those); this list is the
# raster subset accepted for uploads.
# ---------------------------------------------------------------------------

AVATAR_ALLOWED_TYPES_DEFAULT: tuple[str, ...] = (
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
)

AVATAR_DEFAULT_COLORS_DEFAULT: tuple[str, ...] = (
    "#e94560",
    "#4ade80",
    "#fbbf24",
    "#60a5fa",
    "#a78bfa",
    "#f472b6",
)

# ---------------------------------------------------------------------------
# URL prefixes for avatar/icon resources.
# ---------------------------------------------------------------------------

USER_AVATAR_URL_PREFIX: str = "/api/v1/avatars/users"
SERVER_ICON_URL_PREFIX: str = "/api/v1/avatars/servers"

# ---------------------------------------------------------------------------
# Bounded checksum cache tuning (M5). Kept as module-level "private" names so
# existing tests and callers keep resolving them via the package namespace.
# ---------------------------------------------------------------------------

_MAX_CHECKSUM_CACHE_ENTRIES: int = 4096
_CHECKSUM_CACHE_TTL_SEC: float = 1800.0

# ---------------------------------------------------------------------------
# Path-traversal / safe identifier guards.
# ---------------------------------------------------------------------------

_PATH_TRAVERSAL_MARKERS: tuple[str, ...] = ("..", "/", "\\", "\x00")


def is_safe_avatar_identifier(value: str) -> bool:
    """True when ``value`` contains no path-traversal or null-byte markers."""
    return not any(marker in value for marker in _PATH_TRAVERSAL_MARKERS)


__all__ = [
    "AVATAR_MAX_FILE_SIZE_DEFAULT",
    "AVATAR_MAX_DIMENSION_DEFAULT",
    "AVATAR_MAX_PIXELS_DEFAULT",
    "AVATAR_ALLOWED_TYPES_DEFAULT",
    "AVATAR_DEFAULT_COLORS_DEFAULT",
    "USER_AVATAR_URL_PREFIX",
    "SERVER_ICON_URL_PREFIX",
    "_MAX_CHECKSUM_CACHE_ENTRIES",
    "_CHECKSUM_CACHE_TTL_SEC",
    "is_safe_avatar_identifier",
]
