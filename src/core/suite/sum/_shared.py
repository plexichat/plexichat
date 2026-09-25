"""Shared constants, state, errors, and blob stores for PlexiSum.

Split out of ``manager.py`` so every mixin module can import one canonical
copy of module-level state; ``manager.py`` re-exports these names so existing
``from src.core.suite.sum.manager import X`` call sites keep working.
"""

import os
import re
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

MAX_SNAPSHOT_CELLS = 200_000
MAX_CSV_ROWS = 10_000
MAX_CSV_COLUMNS = 100
MAX_INDEXES_PER_SHEET = 8
INDEX_PROMOTION_THRESHOLD = 50

# Structural (row/column) operation bounds.
STRUCTURAL_OPERATION_TYPES = (
    "insert_rows",
    "delete_rows",
    "insert_columns",
    "delete_columns",
)
MAX_STRUCTURAL_CHANGE = 10_000
MAX_STRUCTURAL_CELLS = 200_000

# Snapshots beyond this count are garbage-collected (oldest first) when a
# new snapshot is created.
MAX_SNAPSHOTS_KEPT = 50
# Bound compensation memory during account/workbook purge. A purge that would
# exceed this limit fails before deleting that blob batch, preserving metadata
# pointers for a later retry instead of risking worker OOM.
MAX_PURGE_BACKUP_BYTES = 256 * 1024 * 1024

# Keep this many immutable revision blobs per workbook after a successful
# commit; older revisions are unreachable through any read path (the metadata
# row only tracks the current blob) and are garbage-collected to stop storage
# growing by the full file size on every edit.
REVISION_KEEP_COUNT = 5

# Bounds for the in-process workbook-file read cache. Blobs are immutable and
# addressed by (workbook_id, revision), so cached entries can never go stale.
WORKBOOK_CACHE_MAX_ENTRIES = 32
WORKBOOK_CACHE_MAX_BYTES = 256 * 1024 * 1024


class _BoundedFileCache:
    """Thread-safe bounded LRU for immutable workbook file bytes.

    Every read currently has to fetch the whole ``.plexisum`` file from the
    storage adapter (S3/local); the cache eliminates repeated full-file
    downloads for hot workbooks without ever serving stale data because the
    key includes the immutable revision. Bounded by entry count and total
    bytes so a few large workbooks cannot exhaust worker memory.
    """

    def __init__(
        self,
        max_entries: int = WORKBOOK_CACHE_MAX_ENTRIES,
        max_bytes: int = WORKBOOK_CACHE_MAX_BYTES,
    ) -> None:
        self._entries: "OrderedDict[Tuple[int, int], bytes]" = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = threading.RLock()

    def get(self, key: Tuple[int, int]) -> Optional[bytes]:
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: Tuple[int, int], value: bytes) -> None:
        with self._lock:
            if key in self._entries:
                self._bytes -= len(self._entries.pop(key))
            self._entries[key] = value
            self._bytes += len(value)
            while (
                len(self._entries) > self._max_entries or self._bytes > self._max_bytes
            ) and self._entries:
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= len(evicted)

    def drop_workbook(self, workbook_id: int) -> None:
        with self._lock:
            stale = [key for key in self._entries if key[0] == int(workbook_id)]
            for key in stale:
                self._bytes -= len(self._entries.pop(key))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0


_WORKBOOK_FILE_CACHE = _BoundedFileCache()

# In-process workbook presence registry: workbook_id -> user_id -> record.
# Clients heartbeat via PUT /workbooks/{id}/presence; stale entries older
# than PRESENCE_TTL_SECONDS are pruned on read. Single-worker by design (the
# workbook file cache above is likewise in-process); multi-worker presence
# would ride the cross-worker Valkey pub/sub introduced for events.
PRESENCE_TTL_SECONDS = 30
_WORKBOOK_PRESENCE: Dict[int, Dict[int, Dict[str, Any]]] = {}
_PRESENCE_LOCK = threading.RLock()


# ── Coalesced write sessions ────────────────────────────────────────────────
# Cell edits are acknowledged durably (main-database ``sum_operations`` log +
# revision CAS) and applied to a live in-memory copy of the workbook; the
# immutable blob is only promoted when the write buffer crosses a threshold.
# This collapses the previous one-full-file-upload-per-edit into one upload per
# flush window while crash safety is preserved by the durable log, which
# materialization replays on top of the stored blob. The registry is module
# level (like ``_WORKBOOK_FILE_CACHE``) so it survives per-request manager
# instances and is shared across the process's workers.

SUM_SESSION_MAX_ENTRIES = 32  # live in-memory workbook copies per process
SUM_FLUSH_MAX_OPS = 25  # unflushed ops before a forced flush
SUM_FLUSH_MAX_MS = 2000  # age of the oldest unflushed op before flush

_WORKBOOK_SESSIONS: "Dict[int, _LiveSession]" = {}
_WORKBOOK_SESSION_LOCKS: "Dict[int, threading.RLock]" = {}
_SESSION_GUARD = threading.RLock()
# Temp-file paths for runtimes without sqlite ``deserialize`` (id -> path),
# removed when the session connection is closed.
_SESSION_TMP_PATHS: "Dict[int, str]" = {}


class _LiveSession:
    """An in-memory workbook copy materialized at the current revision.

    ``loaded_blob_revision`` is the revision the stored blob is materialized
    at; the connection itself always reflects ``current_revision`` because
    unflushed operations are replayed into it. ``pending`` counts operations
    applied locally since the last flush and drives the coalescing window.
    """

    __slots__ = (
        "connection",
        "loaded_blob_revision",
        "pending",
        "last_flush_ms",
        "last_access_ms",
    )

    def __init__(
        self,
        connection: "sqlite3.Connection",
        loaded_blob_revision: int,
    ) -> None:
        self.connection = connection
        self.loaded_blob_revision = int(loaded_blob_revision)
        self.pending = 0
        self.last_flush_ms = int(time.time() * 1000)
        self.last_access_ms = self.last_flush_ms


def _prune_presence(now: Optional[float] = None) -> None:
    """Drop presence records whose heartbeats are older than the TTL."""
    if now is None:
        now = time.time()
    with _PRESENCE_LOCK:
        for workbook_id in list(_WORKBOOK_PRESENCE):
            stale = [
                uid
                for uid, rec in _WORKBOOK_PRESENCE[workbook_id].items()
                if now - float(rec.get("ts", 0)) > PRESENCE_TTL_SECONDS
            ]
            for uid in stale:
                del _WORKBOOK_PRESENCE[workbook_id][uid]
            if not _WORKBOOK_PRESENCE[workbook_id]:
                del _WORKBOOK_PRESENCE[workbook_id]


def _column_letter(index: int) -> str:
    """Convert a zero-based column index to its spreadsheet label (0 -> A)."""
    label = ""
    value = int(index) + 1
    while value > 0:
        remainder = (value - 1) % 26
        label = chr(65 + remainder) + label
        value = (value - 1) // 26
    return label


def _classify_csv_value(
    row_index: int, column_index: int, value: Any
) -> Dict[str, Any]:
    """Infer number/boolean/text types from a CSV cell (mirrors the client)."""
    text = str(value)
    if text == "TRUE":
        return {
            "row": row_index,
            "column": column_index,
            "value_type": "boolean",
            "value": True,
        }
    if text == "FALSE":
        return {
            "row": row_index,
            "column": column_index,
            "value_type": "boolean",
            "value": False,
        }
    # ASCII digits only, matching the formula engine's numeric pattern and the
    # client's CSV classifier: an ASCII ``\d`` would also match the digits of
    # other scripts (and ``float`` reads them), typing an Arabic-Indic numeral
    # as a number here and as text there.
    if re.match(r"^-?[0-9]+(\.[0-9]+)?\Z", text.strip()):
        return {
            "row": row_index,
            "column": column_index,
            "value_type": "number",
            "value": float(text),
        }
    return {
        "row": row_index,
        "column": column_index,
        "value_type": "text",
        "value": text,
    }


class SumNotFoundError(LookupError):
    """Raised when a workbook, sheet, or snapshot is unavailable."""


class SumPermissionError(PermissionError):
    """Raised when a caller lacks workbook access."""


class SumValidationError(ValueError):
    """Raised for malformed or over-sized workbook operations."""


class SumConflictError(RuntimeError):
    """Raised when an operation's base revision is stale."""


class SumStorageError(RuntimeError):
    """Raised when the workbook file cannot be stored or retrieved."""


class BlobStore:
    """Storage-adapter boundary for server-authored workbook files."""

    def store(self, data: bytes, key: str) -> str:
        raise NotImplementedError

    def retrieve(self, path: str) -> bytes:
        raise NotImplementedError

    def delete(self, path: str) -> bool:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def size(self, path: str) -> int:
        raise NotImplementedError


class MediaBlobStore(BlobStore):
    """Store workbook files through the media storage adapter (S3/local)."""

    def __init__(self, media: Any) -> None:
        self._media = media

    def store(self, data: bytes, key: str) -> str:
        return self._media.store_blob(data, key)

    def retrieve(self, path: str) -> bytes:
        return self._media.retrieve_blob(path)

    def delete(self, path: str) -> bool:
        try:
            return bool(self._media.delete_blob(path))
        except Exception:
            return False

    def exists(self, path: str) -> bool:
        # Use the media module's public server-blob boundary when available. A
        # Compatibility facades may predate blob_exists(). Retrieve is still a
        # reliable existence probe for those adapters and avoids treating every
        # successful delete as unconfirmed forever.
        checker = getattr(self._media, "blob_exists", None)
        if callable(checker):
            try:
                return bool(checker(path))
            except ValueError:
                # Path-confinement rejection (e.g. a legacy blob_path
                # recorded without the ``plexisum/`` namespace): such a
                # path is not addressable in this store, hence not
                # present in it. Fail closed without treating it live.
                return False
        try:
            self._media.retrieve_blob(path)
            return True
        except FileNotFoundError:
            return False
        except ValueError:
            # Same confinement case on the retrieve fallback path.
            return False
        except Exception:
            # An unavailable/unauthorized backend is not proof of absence.
            # Report present so _delete_blob_confirmed fails closed and keeps
            # the metadata pointer for a later retry.
            return True

    def size(self, path: str) -> int:
        try:
            return int(self._media.get_blob_size(path))
        except Exception:
            return 0


class LocalBlobStore(BlobStore):
    """Filesystem fallback used when media is unavailable (tests, embedded)."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _resolve(self, path: str) -> str:
        if (
            not isinstance(path, str)
            or path.startswith((".", "/", "\\"))
            or ".." in path
        ):
            raise ValueError("Invalid blob path")
        return os.path.join(self._base_dir, path)

    def store(self, data: bytes, key: str) -> str:
        target = self._resolve(key)
        directory = os.path.dirname(target)
        os.makedirs(directory, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".plexisum-", suffix=".tmp", dir=directory, delete=False
            ) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
            return key
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def retrieve(self, path: str) -> bytes:
        with open(self._resolve(path), "rb") as handle:
            return handle.read()

    def delete(self, path: str) -> bool:
        try:
            os.unlink(self._resolve(path))
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def exists(self, path: str) -> bool:
        try:
            return os.path.isfile(self._resolve(path))
        except OSError:
            return False

    def size(self, path: str) -> int:
        try:
            return int(os.path.getsize(self._resolve(path)))
        except OSError:
            return 0
