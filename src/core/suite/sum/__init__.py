"""PlexiSum workbook and high-performance data-layer contracts.

Workbook files are independent SQLite databases (``.plexisum``) stored through
the main file-storage adapter. The main database holds only metadata and ACL.
"""

from typing import Any, Optional

from .manager import (
    BlobStore,
    LocalBlobStore,
    MediaBlobStore,
    SumConflictError,
    SumManager,
    SumNotFoundError,
    SumPermissionError,
    SumStorageError,
    SumValidationError,
)
from .schema import create_tables

_manager: Optional[SumManager] = None


def setup(
    db: Any,
    servers_module: Optional[Any] = None,
    media: Optional[Any] = None,
    storage_dir: Optional[str] = None,
) -> SumManager:
    """Initialize and retain the process-local Sum manager.

    ``media`` is the media module exposing the storage-adapter blob helpers;
    when unavailable (isolated tests, embedded callers) a local directory blob
    store under ``storage_dir`` (default ``<cwd>/data/sum``) is used instead.
    """
    global _manager
    _manager = SumManager(
        db,
        servers_module=servers_module,
        media=media,
        storage_dir=storage_dir,
    )
    return _manager


def get_manager() -> SumManager:
    """Return the initialized Sum manager."""
    if _manager is None:
        raise RuntimeError("PlexiSum is not initialized")
    return _manager


def purge_user_data(user_id: int) -> None:
    """Erase a user's workbook files and metadata (account erasure path)."""
    if _manager is not None:
        _manager.purge_user_data(user_id)


__all__ = [
    "BlobStore",
    "LocalBlobStore",
    "MediaBlobStore",
    "SumConflictError",
    "SumManager",
    "SumNotFoundError",
    "SumPermissionError",
    "SumStorageError",
    "SumValidationError",
    "create_tables",
    "setup",
    "get_manager",
]
