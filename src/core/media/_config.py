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
}

DEFAULT_THUMBNAIL_SIZES = [64, 128, 256, 512]
