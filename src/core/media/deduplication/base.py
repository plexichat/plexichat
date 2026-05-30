"""
Base class for DeduplicationManager.
"""

from typing import Any

import utils.config as config


class DeduplicationManagerBase:
    """Base class providing core initialization and configuration loading."""

    __slots__ = ("_db", "_config")

    def __init__(self, db: Any) -> None:
        """Initialize deduplication manager."""
        self._db = db
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load deduplication configuration."""
        media_config = config.get("media", {})
        dedup_config = media_config.get("deduplication", {})
        phash_config = media_config.get("phash", {})

        return {
            "enabled": dedup_config.get("enabled", True),
            "hash_algorithm": dedup_config.get("hash_algorithm", "sha256"),
            "min_size": dedup_config.get("min_size", 10240),
            "auto_block_threshold": dedup_config.get("auto_block_threshold", 5),
            "phash_enabled": phash_config.get("enabled", True),
            "phash_threshold": phash_config.get("similarity_threshold", 10),
            "phash_algorithm": phash_config.get("algorithm", "phash"),
        }
