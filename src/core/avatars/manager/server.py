"""Server icon operations mixin for the avatars module."""

import time
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger

from src.utils.encryption import generate_snowflake_id

from .protocol import AvatarProtocol


class AvatarServerMixin(AvatarProtocol):
    """Mixin handling all server icon CRUD operations."""

    def get_server_icon_checksum(self, server_id: int) -> Optional[str]:
        """Get server icon checksum for ETag (cached)."""
        cache_key = f"server_icon_checksum:{server_id}"
        cached_checksum = self._get_cached_binary(cache_key)
        if cached_checksum:
            return cached_checksum.decode()

        db = self._get_db()
        row = db.fetch_one(
            "SELECT checksum FROM server_icons WHERE server_id = ?", (server_id,)
        )
        if row:
            self._cache_binary(cache_key, row["checksum"].encode(), ttl=3600)
            return row["checksum"]
        return None

    def upload_server_icon(
        self, server_id: int, image_data: bytes, content_type: str
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
        self._cache_binary(f"server_icon_bin:{server_id}", processed_data)
        self._cache_binary(
            f"server_icon_meta:{server_id}", f"{content_type}|{checksum}".encode()
        )

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

    def get_server_icon(self, server_id: int) -> Optional[Dict[str, Any]]:
        """Get server icon metadata."""
        db = self._get_db()

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

    def get_server_icon_data(self, server_id: int) -> Optional[Tuple[bytes, str, str]]:
        """
        Get server icon binary data (cached).

        Returns: (icon_bytes, content_type, checksum) or None
        """
        # 1. Check cache
        bin_data = self._get_cached_binary(f"server_icon_bin:{server_id}")
        meta_data = self._get_cached_binary(f"server_icon_meta:{server_id}")

        if bin_data and meta_data:
            try:
                content_type, checksum = meta_data.decode().split("|")
                return bin_data, content_type, checksum
            except Exception:
                pass  # Fall back to DB if cache format is weird

        # 2. Fetch from DB
        db = self._get_db()
        row = db.fetch_one(
            "SELECT icon_data, content_type, checksum FROM server_icons WHERE server_id = ?",
            (server_id,),
        )

        if not row:
            return None

        # 3. Cache result
        self._cache_binary(f"server_icon_bin:{server_id}", row["icon_data"])
        self._cache_binary(
            f"server_icon_meta:{server_id}",
            f"{row['content_type']}|{row['checksum']}".encode(),
        )

        return row["icon_data"], row["content_type"], row["checksum"]

    def get_server_icon_url(self, server_id: int) -> Optional[str]:
        """Get server icon URL if exists."""
        db = self._get_db()

        row = db.fetch_one(
            "SELECT id FROM server_icons WHERE server_id = ?", (server_id,)
        )
        if row:
            return f"/api/v1/avatars/servers/{server_id}"
        return None

    def delete_server_icon(self, server_id: int) -> bool:
        """Delete server icon."""
        db = self._get_db()

        result = db.execute(
            "DELETE FROM server_icons WHERE server_id = ?", (server_id,)
        )

        # Clear icon_url in servers
        db.execute("UPDATE srv_servers SET icon_url = NULL WHERE id = ?", (server_id,))

        # Invalidate cache
        self._delete_cached_binary(f"server_icon_bin:{server_id}")
        self._delete_cached_binary(f"server_icon_meta:{server_id}")

        deleted = result.rowcount if hasattr(result, "rowcount") else 0
        if deleted:
            logger.info(f"Icon deleted for server {server_id}")

        return deleted > 0
