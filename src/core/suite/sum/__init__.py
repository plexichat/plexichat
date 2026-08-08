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

    ``media`` is the media module exposing the storage-adapter blob helpers.
    In development/test mode an explicit ``storage_dir`` may be supplied for
    isolated tests; production requires the configured media adapter.
    """
    global _manager
    # Migration 000 is intentionally authoritative for fresh installations,
    # but applied migrations are not rerun on every boot. Re-apply this
    # idempotent metadata schema step so existing databases receive additive
    # Sum tables and the artifact_id compatibility column before any manager
    # query uses them. Workbook cells remain exclusively in .plexisum files.
    create_tables(db)
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
    "purge_user_data",
    "create_tables",
    "setup",
    "get_manager",
]
