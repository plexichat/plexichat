"""Database tables mixin for the avatars module."""

from typing import Any

import utils.logger as logger

from .protocol import AvatarProtocol


class AvatarTablesMixin(AvatarProtocol):
    """Mixin handling database table creation."""

    def create_tables(self, db: Any) -> None:
        """Create avatar tables."""

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

    def _create_tables(self) -> None:
        """Create avatar tables."""
        db = self._get_db()
        self.create_tables(db)
