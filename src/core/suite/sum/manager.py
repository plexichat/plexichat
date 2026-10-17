"""PlexiSum workbook service — file-backed, permission-aware.

Each workbook is an independent SQLite database file (``.plexisum``) stored
through the main file-storage adapter (S3/local via the media blob boundary).
The main database never stores workbook cells; it keeps only identity,
ownership, sharing ACL, range permissions, snapshot pointers, and the revision
counter used for optimistic concurrency.

Every read opens the workbook file from storage and every mutation writes a
new immutable versioned blob before the metadata row advances, which gives
readers snapshot isolation and writers a compare-and-swap gate.

This module is the stable import facade: the manager implementation is split
across ``manager_*.py`` mixin modules (each under 1000 lines) and shared
module-level state lives in ``_shared.py``. All historical public and private
names are re-exported here so existing call sites keep working.
"""

from src.core.base import BaseManager

from ._shared import (  # noqa: F401  (facade re-exports)
    INDEX_PROMOTION_THRESHOLD,
    MAX_CSV_COLUMNS,
    MAX_CSV_ROWS,
    MAX_INDEXES_PER_SHEET,
    MAX_PURGE_BACKUP_BYTES,
    MAX_SNAPSHOT_CELLS,
    MAX_SNAPSHOTS_KEPT,
    MAX_STRUCTURAL_CELLS,
    MAX_STRUCTURAL_CHANGE,
    PRESENCE_TTL_SECONDS,
    REVISION_KEEP_COUNT,
    STRUCTURAL_OPERATION_TYPES,
    SUM_FLUSH_MAX_MS,
    SUM_FLUSH_MAX_OPS,
    SUM_SESSION_MAX_ENTRIES,
    WORKBOOK_CACHE_MAX_BYTES,
    WORKBOOK_CACHE_MAX_ENTRIES,
    BlobStore,
    LocalBlobStore,
    MediaBlobStore,
    SumConflictError,
    SumNotFoundError,
    SumPermissionError,
    SumStorageError,
    SumValidationError,
    _BoundedFileCache,
    _LiveSession,
    _PRESENCE_LOCK,
    _SESSION_GUARD,
    _SESSION_TMP_PATHS,
    _WORKBOOK_FILE_CACHE,
    _WORKBOOK_PRESENCE,
    _WORKBOOK_SESSION_LOCKS,
    _WORKBOOK_SESSIONS,
    _classify_csv_value,
    _column_letter,
    _prune_presence,
)
from .manager_admin import SumManagerAdmin
from .manager_base import SumManagerBase
from .manager_data import SumManagerData
from .manager_exchange import SumManagerExchange
from .manager_features import SumManagerFeatures
from .manager_operations import SumManagerOperations
from .manager_revisions import SumManagerRevisions
from .manager_sessions import SumManagerSessions
from .manager_snapshots import SumManagerSnapshots
from .manager_viewport import SumManagerViewport
from .manager_workbooks import SumManagerWorkbooks


class SumManager(
    SumManagerAdmin,
    SumManagerExchange,
    SumManagerFeatures,
    SumManagerData,
    SumManagerSnapshots,
    SumManagerOperations,
    SumManagerViewport,
    SumManagerWorkbooks,
    SumManagerRevisions,
    SumManagerSessions,
    SumManagerBase,
    BaseManager,
):
    """Authoritative file-backed bounded data layer for PlexiSum."""

    MAX_VIEWPORT_CELLS = 5000
    MAX_OPERATION_CELLS = 2000


__all__ = [
    "INDEX_PROMOTION_THRESHOLD",
    "MAX_CSV_COLUMNS",
    "MAX_CSV_ROWS",
    "MAX_INDEXES_PER_SHEET",
    "MAX_PURGE_BACKUP_BYTES",
    "MAX_SNAPSHOT_CELLS",
    "MAX_SNAPSHOTS_KEPT",
    "MAX_STRUCTURAL_CELLS",
    "MAX_STRUCTURAL_CHANGE",
    "PRESENCE_TTL_SECONDS",
    "REVISION_KEEP_COUNT",
    "STRUCTURAL_OPERATION_TYPES",
    "SUM_FLUSH_MAX_MS",
    "SUM_FLUSH_MAX_OPS",
    "SUM_SESSION_MAX_ENTRIES",
    "WORKBOOK_CACHE_MAX_BYTES",
    "WORKBOOK_CACHE_MAX_ENTRIES",
    "BlobStore",
    "LocalBlobStore",
    "MediaBlobStore",
    "SumConflictError",
    "SumManager",
    "SumNotFoundError",
    "SumPermissionError",
    "SumStorageError",
    "SumValidationError",
    "_BoundedFileCache",
    "_LiveSession",
    "_SESSION_GUARD",
    "_SESSION_TMP_PATHS",
    "_WORKBOOK_FILE_CACHE",
    "_WORKBOOK_PRESENCE",
    "_WORKBOOK_SESSION_LOCKS",
    "_WORKBOOK_SESSIONS",
    "_classify_csv_value",
    "_column_letter",
    "_prune_presence",
]
