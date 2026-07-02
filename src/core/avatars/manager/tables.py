"""Database tables mixin for the avatars module."""

from typing import Any

import utils.logger as logger

from .protocol import AvatarProtocol


class AvatarTablesMixin(AvatarProtocol):
    """Mixin handling database table creation."""

    def create_tables(self, db: Any) -> None:
        """Create avatar tables.

        Uses the canonical schema from :mod:`src.core.avatars.schema` so the
        manager path and the migration-000 path produce identical DDL.
        """
        from src.core.avatars.schema import create_tables

        create_tables(db)
        logger.info("Avatar tables created successfully")

    def _create_tables(self) -> None:
        """Create avatar tables."""
        db = self._get_db()
        self.create_tables(db)
