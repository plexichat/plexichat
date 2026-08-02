"""PlexiSum workbook and high-performance data-layer contracts."""

from typing import Any, Optional

from .manager import (
    SumConflictError,
    SumManager,
    SumNotFoundError,
    SumPermissionError,
    SumValidationError,
)
from .schema import create_tables

_manager: Optional[SumManager] = None


def setup(db: Any, servers_module: Optional[Any] = None) -> SumManager:
    """Initialize and retain the process-local Sum manager."""
    global _manager
    _manager = SumManager(db, servers_module=servers_module)
    return _manager


def get_manager() -> SumManager:
    """Return the initialized Sum manager."""
    if _manager is None:
        raise RuntimeError("PlexiSum is not initialized")
    return _manager


__all__ = [
    "SumConflictError",
    "SumManager",
    "SumNotFoundError",
    "SumPermissionError",
    "SumValidationError",
    "create_tables",
    "setup",
    "get_manager",
]
