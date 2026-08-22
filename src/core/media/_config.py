"""
Media module configuration defaults.

These are the fallback values used when the global config does not
provide media-specific overrides.
"""

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
    # SECURITY: the "other" category is an explicit allow-list (fail closed).
    # It contains only inert, non-browser-renderable types: the neutral
    # binary fallback used by clients for unknown MIME types and first-party
    # container formats stored via the media pipeline. Renderable types such
    # as text/html must NEVER be added here.
    "other": [
        "application/octet-stream",
        "application/x-plexisum",
    ],
}

DEFAULT_THUMBNAIL_SIZES = [64, 128, 256, 512]
