"""Setup mixin for the avatars module."""

from typing import Any

import utils.config as config
import utils.logger as logger

from .protocol import AvatarProtocol


class AvatarSetupMixin(AvatarProtocol):
    """Mixin handling module initialization and configuration."""

    DEFAULT_MAX_SIZE = 512  # 512x512 pixels
    DEFAULT_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    DEFAULT_ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]

    def __init__(self, db: Any = None) -> None:
        # AvatarProtocol has no __init__; object.__init__ takes 0 args.
        # The setup() helper is the canonical entry point — keep state init here.
        self._db = db
        self._setup_complete = False

    def setup(self, db: Any) -> None:
        """Initialize the avatars module."""
        self._db = db
        self._setup_complete = True
        logger.info("Avatars module initialized")

    def is_setup(self) -> bool:
        """Check if module is initialized."""
        return self._setup_complete

    def _get_db(self):
        """Get database instance."""
        if not self._setup_complete:
            raise RuntimeError(
                "Avatars module not initialized. Call avatars.setup(db) first."
            )
        if self._db is None:
            raise RuntimeError("Avatars database not set")
        return self._db

    def _get_config(self, key: str, default: Any = None) -> Any:
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

    def _get_max_size(self) -> int:
        """Get max avatar dimension from config."""
        return self._get_config("max_size", self.DEFAULT_MAX_SIZE)

    def _get_max_file_size(self) -> int:
        """Get max file size from config."""
        return self._get_config("max_file_size", self.DEFAULT_MAX_FILE_SIZE)

    def _get_allowed_types(self) -> list:
        """Get allowed content types from config."""
        return self._get_config("allowed_types", self.DEFAULT_ALLOWED_TYPES)
