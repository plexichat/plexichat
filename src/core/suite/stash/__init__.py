"""PlexiStash metadata storage and authorization service."""

from typing import Any, Optional

from .manager import (
    StashManager,
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)
from .schema import create_tables

_manager: Optional[StashManager] = None


def setup(db: Any, servers_module: Optional[Any] = None) -> StashManager:
    """Initialize Stash metadata tables and return the shared manager."""
    global _manager
    _manager = StashManager(db, servers_module=servers_module)
    return _manager


def get_manager() -> StashManager:
    """Return the initialized manager."""
    if _manager is None:
        raise RuntimeError("PlexiStash is not initialized")
    return _manager


__all__ = [
    "StashManager",
    "StashNotFoundError",
    "StashPermissionError",
    "StashValidationError",
    "create_tables",
    "setup",
    "get_manager",
]
