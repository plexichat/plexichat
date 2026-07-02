"""User avatar operations mixin for the avatars module."""

import time
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger

from src.utils.encryption import generate_snowflake_id

from .protocol import AvatarProtocol


class AvatarUserMixin(AvatarProtocol):
    """Mixin handling all user avatar CRUD operations."""

    def get_user_avatar_checksum(self, user_id: int) -> Optional[str]:
        """Get avatar checksum for ETag (cached)."""
        cache_key = f"user_avatar_checksum:{user_id}"
        cached_checksum = self._get_cached_binary(cache_key)
        if cached_checksum:
            return cached_checksum.decode()

        db = self._get_db()
        row = db.fetch_one(
            "SELECT checksum FROM user_avatars WHERE user_id = ?", (user_id,)
        )
        if row:
            self._cache_binary(cache_key, row["checksum"].encode(), ttl=3600)
            return row["checksum"]
        return None

    def upload_user_avatar(
        self, user_id: int, image_data: bytes, content_type: str
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
        db = self._get_db()

        # Validate content type
        if not self._validate_content_type(content_type):
            raise ValueError(
                f"Content type '{content_type}' not allowed. Allowed: {self._get_allowed_types()}"
            )

        # Validate file size
        max_file_size = self._get_max_file_size()
        if len(image_data) > max_file_size:
            raise ValueError(
                f"File too large. Max size: {max_file_size // (1024 * 1024)}MB"
            )

        # Process image
        processed_data, width, height, is_animated = self._process_image(
            image_data, content_type
        )

        # Compute checksum
        checksum = self._compute_checksum(processed_data)

        # Check if avatar already exists
        existing = db.fetch_one(
            "SELECT id FROM user_avatars WHERE user_id = ?", (user_id,)
        )

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

        # Update user's avatar_url in auth_users
        avatar_url = f"/api/v1/avatars/users/{user_id}"
        db.execute(
            "UPDATE auth_users SET avatar_url = ? WHERE id = ?", (avatar_url, user_id)
        )

        # Cache the binary data and checksum
        self._cache_binary(f"user_avatar_bin:{user_id}", processed_data)
        self._cache_binary(
            f"user_avatar_meta:{user_id}", f"{content_type}|{checksum}".encode()
        )

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

    def get_user_avatar(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user avatar metadata."""
        db = self._get_db()

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

    def get_user_avatar_data(self, user_id: int) -> Optional[Tuple[bytes, str, str]]:
        """
        Get user avatar binary data (cached).

        Returns: (avatar_bytes, content_type, checksum) or None
        """
        # 1. Check cache
        bin_data = self._get_cached_binary(f"user_avatar_bin:{user_id}")
        meta_data = self._get_cached_binary(f"user_avatar_meta:{user_id}")

        if bin_data and meta_data:
            try:
                content_type, checksum = meta_data.decode().split("|")
                return bin_data, content_type, checksum
            except Exception:
                pass  # Fall back to DB if cache format is weird

        # 2. Fetch from DB
        db = self._get_db()
        row = db.fetch_one(
            "SELECT avatar_data, content_type, checksum FROM user_avatars WHERE user_id = ?",
            (user_id,),
        )

        if not row:
            return None

        # 3. Cache result
        self._cache_binary(f"user_avatar_bin:{user_id}", row["avatar_data"])
        self._cache_binary(
            f"user_avatar_meta:{user_id}",
            f"{row['content_type']}|{row['checksum']}".encode(),
        )

        return row["avatar_data"], row["content_type"], row["checksum"]

    def get_user_avatar_url(self, user_id: int) -> Optional[str]:
        """Get user avatar URL if exists."""
        db = self._get_db()

        row = db.fetch_one("SELECT id FROM user_avatars WHERE user_id = ?", (user_id,))
        if row:
            return f"/api/v1/avatars/users/{user_id}"
        return None

    def delete_user_avatar(self, user_id: int) -> bool:
        """Delete user avatar."""
        db = self._get_db()

        result = db.execute("DELETE FROM user_avatars WHERE user_id = ?", (user_id,))

        # Clear avatar_url in auth_users
        db.execute("UPDATE auth_users SET avatar_url = NULL WHERE id = ?", (user_id,))

        # Invalidate cache
        self._delete_cached_binary(f"user_avatar_bin:{user_id}")
        self._delete_cached_binary(f"user_avatar_meta:{user_id}")

        deleted = result.rowcount if hasattr(result, "rowcount") else 0
        if deleted:
            logger.info(f"Avatar deleted for user {user_id}")

        return deleted > 0
