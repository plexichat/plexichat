"""
Avatars Module - Manages user and server avatars stored in database.

This module provides:
- Avatar upload with automatic resizing
- Database BLOB storage for avatars
- Configurable max dimensions
- Support for user avatars and server icons
- Animated GIF support (optional)

Usage:
    from src.core import avatars
    avatars.setup(db)

    # Upload user avatar
    avatar = avatars.upload_user_avatar(user_id, image_bytes, "image/png")

    # Get avatar URL
    url = avatars.get_user_avatar_url(user_id)

    # Get avatar bytes for serving
    data, content_type = avatars.get_avatar_data(avatar_id)
"""

import io
import time
import hashlib
from typing import Optional, Tuple, Dict, Any

import utils.logger as logger
import utils.config as config

from src.utils.encryption import generate_snowflake_id
from src.core.database import get_client, redis_available

_db: Any = None
_setup_complete = False

# Default configuration
DEFAULT_MAX_SIZE = 512  # 512x512 pixels
DEFAULT_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
DEFAULT_ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]


def setup(db: Any) -> None:
    """Initialize the avatars module."""
    global _db, _setup_complete

    _db = db
    _setup_complete = True  # Set before _create_tables so _get_db() works
    _create_tables()
    logger.info("Avatars module initialized")


def is_setup() -> bool:
    """Check if module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError(
            "Avatars module not initialized. Call avatars.setup(db) first."
        )
    if _db is None:
        raise RuntimeError("Avatars database not set")
    return _db


# === Caching Helpers ===

def _cache_binary(key: str, data: bytes, ttl: int = 3600) -> None:
    """Cache binary data in Redis."""
    if not redis_available():
        return
    try:
        client = get_client()
        if client:
            client.set_bin(key, data, ttl=ttl)
    except Exception as e:
        logger.debug(f"Failed to cache binary data for {key}: {e}")


def _get_cached_binary(key: str) -> Optional[bytes]:
    """Get cached binary data from Redis."""
    if not redis_available():
        return None
    try:
        client = get_client()
        if client:
            return client.get_bin(key)
    except Exception as e:
        logger.debug(f"Failed to get cached binary data for {key}: {e}")
    return None


def _delete_cached_binary(key: str) -> None:
    """Delete cached binary data from Redis."""
    if not redis_available():
        return
    try:
        client = get_client()
        if client:
            client.delete(key)
    except Exception as e:
        logger.debug(f"Failed to delete cached binary data for {key}: {e}")


def get_user_avatar_checksum(user_id: int) -> Optional[str]:
    """Get avatar checksum for ETag."""
    db = _get_db()
    row = db.fetch_one("SELECT checksum FROM user_avatars WHERE user_id = ?", (user_id,))
    return row["checksum"] if row else None


def get_server_icon_checksum(server_id: int) -> Optional[str]:
    """Get server icon checksum for ETag."""
    db = _get_db()
    row = db.fetch_one("SELECT checksum FROM server_icons WHERE server_id = ?", (server_id,))
    return row["checksum"] if row else None


def _get_config(key: str, default: Any = None) -> Any:
    """Get avatars configuration value."""
    avatars_config = config.get("avatars", {})
    keys = key.split(".")
    value = avatars_config
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k, default)
        else:
            return default
    return value if value is not None else default


def _create_tables() -> None:
    """Create avatar tables."""
    db = _get_db()

    # User avatars table
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_avatars (
            id BIGINT PRIMARY KEY,
            user_id BIGINT NOT NULL UNIQUE,
            avatar_data BYTEA NOT NULL,
            content_type TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            size INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            animated INTEGER NOT NULL DEFAULT 0,
            uploaded_at BIGINT NOT NULL
        )
    """)

    # Server icons table - no FK constraint since servers table may not exist yet
    db.execute("""
        CREATE TABLE IF NOT EXISTS server_icons (
            id BIGINT PRIMARY KEY,
            server_id BIGINT NOT NULL UNIQUE,
            icon_data BYTEA NOT NULL,
            content_type TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            size INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            animated INTEGER NOT NULL DEFAULT 0,
            uploaded_at BIGINT NOT NULL
        )
    """)

    # Indexes
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_avatars_user ON user_avatars(user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_server_icons_server ON server_icons(server_id)"
    )

    logger.info("Avatar tables created successfully")


def _get_max_size() -> int:
    """Get max avatar dimension from config."""
    return _get_config("max_size", DEFAULT_MAX_SIZE)


def _get_max_file_size() -> int:
    """Get max file size from config."""
    return _get_config("max_file_size", DEFAULT_MAX_FILE_SIZE)


def _get_allowed_types() -> list:
    """Get allowed content types from config."""
    return _get_config("allowed_types", DEFAULT_ALLOWED_TYPES)


def _validate_content_type(content_type: str) -> bool:
    """Validate content type is allowed."""
    allowed = _get_allowed_types()
    return content_type.lower() in [t.lower() for t in allowed]


def _detect_content_type(image_data: bytes, fallback: str) -> str:
    """Detect actual content type from magic bytes."""
    signatures = {
        b"\xff\xd8\xff": "image/jpeg",
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"RIFF": "image/webp",
    }

    for sig, mime in signatures.items():
        if image_data.startswith(sig):
            if sig == b"RIFF":
                if len(image_data) > 12 and image_data[8:12] == b"WEBP":
                    return "image/webp"
                return fallback
            return mime

    return fallback


def _process_image(
    image_data: bytes, content_type: str
) -> Tuple[bytes, int, int, bool]:
    """
    Process and resize image if needed.

    Returns: (processed_bytes, width, height, is_animated)
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed, storing avatar without processing")
        return image_data, 0, 0, False

    # Security: Prevent decompression bombs
    # Use same default as Media module (~178MP)
    max_pixels = _get_config("max_pixels", 178956970)
    Image.MAX_IMAGE_PIXELS = max_pixels

    # Do not allow images with more than 16k width/height
    max_dim = _get_config("max_dimension", 16384)

    # Detect actual content type from bytes to prevent spoofing
    actual_type = _detect_content_type(image_data, content_type)
    if actual_type != content_type:
        logger.info(
            f"Avatar: Detected actual type {actual_type} for file claimed as {content_type}"
        )
        content_type = actual_type

    # Open image (lazy)
    try:
        img = Image.open(io.BytesIO(image_data))

        # Security: Validate dimensions before processing
        width, height = img.size
        if width > max_dim or height > max_dim:
            raise ValueError(
                f"Image dimensions ({width}x{height}) exceed maximum allowed ({max_dim}x{max_dim})"
            )

        if width * height > max_pixels:
            raise ValueError(
                f"Image has too many pixels ({width * height}) - maximum is {max_pixels}"
            )

        original_format = img.format
        n_frames = getattr(img, "n_frames", 1)
        is_animated = bool(getattr(img, "is_animated", False)) or (n_frames > 1)
    except Exception as e:
        if isinstance(e, ValueError):
            raise e
        logger.error(f"Failed to open avatar image: {e}")
        raise ValueError(f"Invalid image file: {e}")

    max_size = _get_max_size()

    # Check if resize needed
    if width > max_size or height > max_size:
        # Calculate new dimensions maintaining aspect ratio
        if width > height:
            new_width = max_size
            new_height = int(height * (max_size / width))
        else:
            new_height = max_size
            new_width = int(width * (max_size / height))

        if is_animated and original_format == "GIF":
            # Handle animated GIF - resize all frames
            frames = []
            durations = []

            try:
                for frame_num in range(n_frames):
                    img.seek(frame_num)
                    frame = img.copy()
                    frame = frame.resize(
                        (new_width, new_height), Image.Resampling.LANCZOS
                    )
                    frames.append(frame)
                    durations.append(img.info.get("duration", 100))

                # Save animated GIF
                output = io.BytesIO()
                frames[0].save(
                    output,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=img.info.get("loop", 0),
                )
                return output.getvalue(), new_width, new_height, True
            except Exception as e:
                logger.warning(
                    f"Failed to process animated GIF: {e}, using first frame"
                )
                img.seek(0)
                is_animated = False

        # Resize static image
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        width, height = new_width, new_height

    # Convert to appropriate format for output
    output = io.BytesIO()

    if content_type == "image/gif" and is_animated:
        img.save(output, format="GIF")
    elif content_type == "image/png" or img.mode == "RGBA":
        img.save(output, format="PNG", optimize=True)
        content_type = "image/png"
    elif content_type == "image/webp":
        img.save(output, format="WEBP", quality=90)
    else:
        # Convert to RGB for JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(output, format="JPEG", quality=90, optimize=True)
        content_type = "image/jpeg"

    return output.getvalue(), width, height, is_animated


def _compute_checksum(data: bytes) -> str:
    """Compute SHA-256 checksum."""
    return hashlib.sha256(data).hexdigest()


# === User Avatars ===


def upload_user_avatar(
    user_id: int, image_data: bytes, content_type: str
) -> Dict[str, Any]:
    """
    Upload or update a user's avatar.

    Args:
        user_id: User ID
        image_data: Raw image bytes
        content_type: MIME type

    Returns:
        Dict with avatar info including URL
    """
    db = _get_db()

    # Validate content type
    if not _validate_content_type(content_type):
        raise ValueError(
            f"Content type '{content_type}' not allowed. Allowed: {_get_allowed_types()}"
        )

    # Validate file size
    max_file_size = _get_max_file_size()
    if len(image_data) > max_file_size:
        raise ValueError(
            f"File too large. Max size: {max_file_size // (1024 * 1024)}MB"
        )

    # Process image
    processed_data, width, height, is_animated = _process_image(
        image_data, content_type
    )

    # Compute checksum
    checksum = _compute_checksum(processed_data)

    # Check if avatar already exists
    existing = db.fetch_one("SELECT id FROM user_avatars WHERE user_id = ?", (user_id,))

    now = int(time.time() * 1000)

    if existing:
        # Update existing avatar
        db.execute(
            """
            UPDATE user_avatars 
            SET avatar_data = ?, content_type = ?, width = ?, height = ?, 
                size = ?, checksum = ?, animated = ?, uploaded_at = ?
            WHERE user_id = ?
        """,
            (
                processed_data,
                content_type,
                width,
                height,
                len(processed_data),
                checksum,
                1 if is_animated else 0,
                now,
                user_id,
            ),
        )
        avatar_id = existing["id"]
    else:
        # Insert new avatar
        avatar_id = generate_snowflake_id()
        db.execute(
            """
            INSERT INTO user_avatars 
            (id, user_id, avatar_data, content_type, width, height, size, checksum, animated, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                avatar_id,
                user_id,
                processed_data,
                content_type,
                width,
                height,
                len(processed_data),
                checksum,
                1 if is_animated else 0,
                now,
            ),
        )

    # Update user's avatar_url in auth_users to point to new endpoint
    avatar_url = f"/api/v1/avatars/users/{user_id}"
    db.execute(
        "UPDATE auth_users SET avatar_url = ? WHERE id = ?", (avatar_url, user_id)
    )

    # Cache the binary data and checksum
    _cache_binary(f"user_avatar_bin:{user_id}", processed_data)
    _cache_binary(f"user_avatar_meta:{user_id}", f"{content_type}|{checksum}".encode())

    logger.info(
        f"Avatar uploaded for user {user_id}: {width}x{height}, {len(processed_data)} bytes"
    )

    return {
        "id": str(avatar_id),
        "user_id": str(user_id),
        "url": avatar_url,
        "width": width,
        "height": height,
        "size": len(processed_data),
        "content_type": content_type,
        "animated": is_animated,
    }


def get_user_avatar(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user avatar metadata."""
    db = _get_db()

    row = db.fetch_one(
        """
        SELECT id, user_id, content_type, width, height, size, checksum, animated, uploaded_at
        FROM user_avatars WHERE user_id = ?
    """,
        (user_id,),
    )

    if not row:
        return None

    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "url": f"/api/v1/avatars/users/{user_id}",
        "width": row["width"],
        "height": row["height"],
        "size": row["size"],
        "content_type": row["content_type"],
        "animated": bool(row["animated"]),
        "uploaded_at": row["uploaded_at"],
    }


def get_user_avatar_data(user_id: int) -> Optional[Tuple[bytes, str, str]]:
    """
    Get user avatar binary data (cached).

    Returns: (avatar_bytes, content_type, checksum) or None
    """
    # 1. Check cache
    bin_data = _get_cached_binary(f"user_avatar_bin:{user_id}")
    meta_data = _get_cached_binary(f"user_avatar_meta:{user_id}")
    
    if bin_data and meta_data:
        try:
            content_type, checksum = meta_data.decode().split("|")
            return bin_data, content_type, checksum
        except Exception:
            pass # Fall back to DB if cache format is weird

    # 2. Fetch from DB
    db = _get_db()
    row = db.fetch_one(
        "SELECT avatar_data, content_type, checksum FROM user_avatars WHERE user_id = ?",
        (user_id,),
    )

    if not row:
        return None

    # 3. Cache result
    _cache_binary(f"user_avatar_bin:{user_id}", row["avatar_data"])
    _cache_binary(f"user_avatar_meta:{user_id}", f"{row['content_type']}|{row['checksum']}".encode())

    return row["avatar_data"], row["content_type"], row["checksum"]


def get_user_avatar_url(user_id: int) -> Optional[str]:
    """Get user avatar URL if exists."""
    db = _get_db()

    row = db.fetch_one("SELECT id FROM user_avatars WHERE user_id = ?", (user_id,))
    if row:
        return f"/api/v1/avatars/users/{user_id}"
    return None


def delete_user_avatar(user_id: int) -> bool:
    """Delete user avatar."""
    db = _get_db()

    result = db.execute("DELETE FROM user_avatars WHERE user_id = ?", (user_id,))

    # Clear avatar_url in auth_users
    db.execute("UPDATE auth_users SET avatar_url = NULL WHERE id = ?", (user_id,))

    # Invalidate cache
    _delete_cached_binary(f"user_avatar_bin:{user_id}")
    _delete_cached_binary(f"user_avatar_meta:{user_id}")

    deleted = result.rowcount if hasattr(result, "rowcount") else 0
    if deleted:
        logger.info(f"Avatar deleted for user {user_id}")

    return deleted > 0


# === Server Icons ===


def upload_server_icon(
    server_id: int, image_data: bytes, content_type: str
) -> Dict[str, Any]:
    """
    Upload or update a server's icon.

    Args:
        server_id: Server ID
        image_data: Raw image bytes
        content_type: MIME type

    Returns:
        Dict with icon info including URL
    """
    db = _get_db()

    # Validate content type
    if not _validate_content_type(content_type):
        raise ValueError(
            f"Content type '{content_type}' not allowed. Allowed: {_get_allowed_types()}"
        )

    # Validate file size
    max_file_size = _get_max_file_size()
    if len(image_data) > max_file_size:
        raise ValueError(
            f"File too large. Max size: {max_file_size // (1024 * 1024)}MB"
        )

    # Process image
    processed_data, width, height, is_animated = _process_image(
        image_data, content_type
    )

    # Compute checksum
    checksum = _compute_checksum(processed_data)

    # Check if icon already exists
    existing = db.fetch_one(
        "SELECT id FROM server_icons WHERE server_id = ?", (server_id,)
    )

    now = int(time.time() * 1000)

    if existing:
        # Update existing icon
        db.execute(
            """
            UPDATE server_icons 
            SET icon_data = ?, content_type = ?, width = ?, height = ?, 
                size = ?, checksum = ?, animated = ?, uploaded_at = ?
            WHERE server_id = ?
        """,
            (
                processed_data,
                content_type,
                width,
                height,
                len(processed_data),
                checksum,
                1 if is_animated else 0,
                now,
                server_id,
            ),
        )
        icon_id = existing["id"]
    else:
        # Insert new icon
        icon_id = generate_snowflake_id()
        db.execute(
            """
            INSERT INTO server_icons 
            (id, server_id, icon_data, content_type, width, height, size, checksum, animated, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                icon_id,
                server_id,
                processed_data,
                content_type,
                width,
                height,
                len(processed_data),
                checksum,
                1 if is_animated else 0,
                now,
            ),
        )

    # Update server's icon_url
    icon_url = f"/api/v1/avatars/servers/{server_id}"
    db.execute(
        "UPDATE srv_servers SET icon_url = ? WHERE id = ?", (icon_url, server_id)
    )

    # Cache the binary data and checksum
    _cache_binary(f"server_icon_bin:{server_id}", processed_data)
    _cache_binary(f"server_icon_meta:{server_id}", f"{content_type}|{checksum}".encode())

    logger.info(
        f"Icon uploaded for server {server_id}: {width}x{height}, {len(processed_data)} bytes"
    )

    return {
        "id": str(icon_id),
        "server_id": str(server_id),
        "url": icon_url,
        "width": width,
        "height": height,
        "size": len(processed_data),
        "content_type": content_type,
        "animated": is_animated,
    }


def get_server_icon(server_id: int) -> Optional[Dict[str, Any]]:
    """Get server icon metadata."""
    db = _get_db()

    row = db.fetch_one(
        """
        SELECT id, server_id, content_type, width, height, size, checksum, animated, uploaded_at
        FROM server_icons WHERE server_id = ?
    """,
        (server_id,),
    )

    if not row:
        return None

    return {
        "id": str(row["id"]),
        "server_id": str(row["server_id"]),
        "url": f"/api/v1/avatars/servers/{server_id}",
        "width": row["width"],
        "height": row["height"],
        "size": row["size"],
        "content_type": row["content_type"],
        "animated": bool(row["animated"]),
        "uploaded_at": row["uploaded_at"],
    }


def get_server_icon_data(server_id: int) -> Optional[Tuple[bytes, str, str]]:
    """
    Get server icon binary data (cached).

    Returns: (icon_bytes, content_type, checksum) or None
    """
    # 1. Check cache
    bin_data = _get_cached_binary(f"server_icon_bin:{server_id}")
    meta_data = _get_cached_binary(f"server_icon_meta:{server_id}")
    
    if bin_data and meta_data:
        try:
            content_type, checksum = meta_data.decode().split("|")
            return bin_data, content_type, checksum
        except Exception:
            pass # Fall back to DB if cache format is weird

    # 2. Fetch from DB
    db = _get_db()
    row = db.fetch_one(
        "SELECT icon_data, content_type, checksum FROM server_icons WHERE server_id = ?",
        (server_id,),
    )

    if not row:
        return None

    # 3. Cache result
    _cache_binary(f"server_icon_bin:{server_id}", row["icon_data"])
    _cache_binary(f"server_icon_meta:{server_id}", f"{row['content_type']}|{row['checksum']}".encode())

    return row["icon_data"], row["content_type"], row["checksum"]


def get_server_icon_url(server_id: int) -> Optional[str]:
    """Get server icon URL if exists."""
    db = _get_db()

    row = db.fetch_one("SELECT id FROM server_icons WHERE server_id = ?", (server_id,))
    if row:
        return f"/api/v1/avatars/servers/{server_id}"
    return None


def delete_server_icon(server_id: int) -> bool:
    """Delete server icon."""
    db = _get_db()

    result = db.execute("DELETE FROM server_icons WHERE server_id = ?", (server_id,))

    # Clear icon_url in servers
    db.execute("UPDATE srv_servers SET icon_url = NULL WHERE id = ?", (server_id,))

    # Invalidate cache
    _delete_cached_binary(f"server_icon_bin:{server_id}")
    _delete_cached_binary(f"server_icon_meta:{server_id}")

    deleted = result.rowcount if hasattr(result, "rowcount") else 0
    if deleted:
        logger.info(f"Icon deleted for server {server_id}")

    return deleted > 0


def generate_default_svg(user_id: int, initials: str) -> str:
    """Generate a colorful SVG placeholder avatar based on user ID."""
    # Match frontend colors in ui.js:getAvatarColor
    colors = ["#e94560", "#4ade80", "#fbbf24", "#60a5fa", "#a78bfa", "#f472b6"]

    # Match frontend logic: index = parseInt(String(id).slice(-2), 16) % colors.length
    id_str = str(user_id)
    try:
        # Frontend does String(id).slice(-2) which is the last 2 characters
        # then parseInt(..., 16) which is hex.
        last_two = id_str[-2:] if len(id_str) >= 2 else id_str
        
        # We need to handle cases where last characters aren't valid hex digits
        # (though Snowflake IDs usually are digits, so hex is safe)
        hex_val = int(last_two, 16)
        index = hex_val % len(colors)
    except (ValueError, IndexError):
        # Fallback to simple modulo if hex parsing fails
        index = user_id % len(colors)
        
    color = colors[index]

    return f"""<svg width="128" height="128" viewBox="0 0 128 128" xmlns="http://www.w3.org/2000/svg">
        <rect width="128" height="128" fill="{color}"/>
        <text x="50%" y="50%" font-family="Arial, sans-serif" font-size="48" font-weight="bold" 
              fill="white" text-anchor="middle" dominant-baseline="central">{initials}</text>
    </svg>"""
