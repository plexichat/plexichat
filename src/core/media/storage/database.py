"""
Database BLOB storage backend for small files.

Ideal for:
- Text files from long message conversions (<500KB)
- Small configuration files
- Tiny thumbnails or icons
- Any file under the configured size threshold

Not recommended for:
- Large images, videos, or audio files
- Files that need direct URL streaming
"""

import io
import base64
import hashlib
import threading
from collections import OrderedDict
from typing import Any, BinaryIO, Hashable, Tuple, Optional

import utils.logger as logger

from .base import StorageBackendBase
from ..exceptions import (
    StorageWriteError,
    StorageReadError,
    StorageDeleteError,
)


# Default max size: 512KB - good for text files, small docs
DEFAULT_MAX_SIZE = 512 * 1024

# Bounds for the dedupe caches below. Both are hard ceilings: an unbounded
# process-local index keyed by content would grow for the lifetime of the
# worker as new blobs are uploaded. These numbers are deliberately small;
# the index is an accelerator, not a second source of truth.
MAX_CHECKSUM_INDEX_ENTRIES = 256
MAX_CHECKSUM_INDEX_BYTES = 512 * 1024
MAX_CHECKSUM_MEMO_ENTRIES = 128
MAX_CHECKSUM_MEMO_BYTES = 2 * 1024 * 1024
# Payloads at or above this size are hashed directly. Caching them would copy
# the whole payload into a dict key for little gain (SHA-256 of small buffers
# is already very fast), so the memo only helps the many-small-files case.
MAX_MEMO_PAYLOAD_BYTES = 64 * 1024

_MISSING = object()


class _BoundedCache:
    """Thread-safe LRU bounded by both entry count and total bytes.

    Two independent byte sizers account for keys *and* values. Accounting for
    the key matters here: the checksum memo is keyed by the full payload bytes,
    so a value-only budget (as used by the generic media ``ResultCache``) would
    leave the keys unbounded.

    Keys are always the *complete* content identity (a full SHA-256 checksum,
    or the exact payload bytes), never a truncated prefix/suffix fingerprint.
    A truncated fingerprint would let two different contents collide into one
    cache entry and make dedupe return the wrong object; exact keys cannot.

    Writes that mutate storage explicitly invalidate affected entries (see
    ``invalidate``), so the cache never serves a path for a blob that was
    deleted or overwritten.
    """

    def __init__(
        self,
        maxsize: int,
        max_bytes: int,
        key_sizer=None,
        value_sizer=None,
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        self._max_bytes = max(1, int(max_bytes))
        self._key_sizer = key_sizer or (lambda k: 1)
        self._value_sizer = value_sizer or (lambda v: 1)
        self._cache: "OrderedDict[Hashable, Any]" = OrderedDict()
        self._sizes: dict = {}
        self._bytes = 0
        self._lock = threading.Lock()
        self._stats = {"hits": 0, "misses": 0, "puts": 0, "invalidations": 0}

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def bytes_used(self) -> int:
        with self._lock:
            return self._bytes

    def clear(self) -> None:
        """Drop every entry (used after bulk mutations such as orphan cleanup)."""
        with self._lock:
            self._cache.clear()
            self._sizes.clear()
            self._bytes = 0

    def get(self, key: Hashable) -> Any:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._stats["hits"] += 1
                return self._cache[key]
            self._stats["misses"] += 1
            return _MISSING

    def put(self, key: Hashable, value: Any) -> None:
        cost = max(1, int(self._key_sizer(key))) + max(1, int(self._value_sizer(value)))
        if cost > self._max_bytes:
            # A single entry that alone busts the budget is never cached.
            return
        with self._lock:
            if key in self._cache:
                self._bytes -= self._sizes.pop(key, 0)
            self._cache[key] = value
            self._sizes[key] = cost
            self._bytes += cost
            self._cache.move_to_end(key)
            self._stats["puts"] += 1
            while self._cache and (
                len(self._cache) > self._maxsize or self._bytes > self._max_bytes
            ):
                oldest, _ = self._cache.popitem(last=False)
                self._bytes -= self._sizes.pop(oldest, 0)

    def invalidate(self, key: Hashable, only_if: Any = _MISSING) -> None:
        """Remove ``key``; if ``only_if`` is given, only when its value matches.

        ``only_if`` avoids dropping a still-valid mapping when two paths share
        one checksum and only one of them is being removed.
        """
        with self._lock:
            if key not in self._cache:
                return
            if only_if is not _MISSING and self._cache[key] != only_if:
                return
            del self._cache[key]
            self._bytes -= self._sizes.pop(key, 0)
            self._stats["invalidations"] += 1


class DatabaseStorage(StorageBackendBase):
    """
    Database BLOB storage backend.

    Stores file content directly in the database as base64-encoded BLOBs.
    Best for small files where simplicity and single-source-of-truth matter
    more than raw performance.
    """

    def __init__(
        self,
        db,
        base_url: str = "/api/v1/media/blob",
        max_size: int = DEFAULT_MAX_SIZE,
    ):
        """
        Initialize database storage.

        Args:
            db: Database instance (must be connected)
            base_url: Base URL for serving files via API
            max_size: Maximum file size in bytes (default 512KB)
        """
        self._db = db
        self._base_url = base_url.rstrip("/")
        self._max_size = max_size

        # Dedupe accelerator caches. ``_checksum_index`` maps a full SHA-256
        # checksum to a blob path so repeated get_by_checksum() calls avoid a
        # DB round trip. ``_checksum_memo`` memoises SHA-256 by full payload
        # bytes so re-storing identical content does not re-hash it. Both are
        # bounded and invalidated on every mutating path below.
        self._checksum_index = _BoundedCache(
            MAX_CHECKSUM_INDEX_ENTRIES,
            MAX_CHECKSUM_INDEX_BYTES,
            key_sizer=len,
            value_sizer=len,
        )
        self._checksum_memo = _BoundedCache(
            MAX_CHECKSUM_MEMO_ENTRIES,
            MAX_CHECKSUM_MEMO_BYTES,
            key_sizer=len,
            value_sizer=len,
        )

        self._ensure_table()
        logger.debug(f"DatabaseStorage initialized (max_size={max_size})")

    def _ensure_table(self):
        """Create the blob storage table if it doesn't exist."""
        schema = """
            CREATE TABLE IF NOT EXISTS media_blobs (
                path TEXT PRIMARY KEY,
                content BLOB NOT NULL,
                content_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """
        # Convert schema types for PostgreSQL compatibility (BLOB -> BYTEA)
        converted = (
            self._db.convert_schema(schema)
            if hasattr(self._db, "convert_schema")
            else schema
        )
        self._db.execute(converted)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_blobs_checksum 
            ON media_blobs(checksum)
        """)

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        import time

        return int(time.time() * 1000)

    def _compute_checksum(self, data: bytes) -> str:
        """Compute SHA-256 checksum.

        Repeated identical payloads reuse a bounded memo keyed by the *full*
        bytes, so no two different contents can ever share an entry. Oversized
        payloads are hashed directly to keep the key memory bounded.
        """
        if not isinstance(data, bytes):
            data = bytes(data)
        if len(data) >= MAX_MEMO_PAYLOAD_BYTES:
            return hashlib.sha256(data).hexdigest()

        cached = self._checksum_memo.get(data)
        if cached is not _MISSING:
            return cached
        checksum = hashlib.sha256(data).hexdigest()
        self._checksum_memo.put(data, checksum)
        return checksum

    def store(self, file_data: bytes, path: str, content_type: str) -> str:
        """Store file data in database."""
        size = len(file_data)

        if size > self._max_size:
            raise StorageWriteError(
                f"File size {size} exceeds database storage limit {self._max_size}. "
                f"Use local or S3 storage for larger files.",
                "database",
            )

        checksum = self._compute_checksum(file_data)
        now = self._get_timestamp()

        try:
            # Check if path exists (update) or new (insert). Fetch the old
            # checksum so an overwrite can invalidate the dedupe index entry
            # that pointed at this path with the previous content.
            existing = self._db.fetch_one(
                "SELECT checksum FROM media_blobs WHERE path = ?", (path,)
            )
            old_checksum = None
            if existing:
                old_checksum = (
                    existing["checksum"] if isinstance(existing, dict) else existing[0]
                )

            if existing:
                self._db.execute(
                    """UPDATE media_blobs 
                       SET content = ?, content_type = ?, size = ?, 
                           checksum = ?, updated_at = ?
                       WHERE path = ?""",
                    (file_data, content_type, size, checksum, now, path),
                )
            else:
                self._db.execute(
                    """INSERT INTO media_blobs 
                       (path, content, content_type, size, checksum, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (path, file_data, content_type, size, checksum, now, now),
                )

            # Refresh the dedupe index only after the write succeeded. If the
            # path previously held different content, drop the stale mapping
            # (but only if it still points here - another path may legitimately
            # share the old checksum).
            if old_checksum and old_checksum != checksum:
                self._checksum_index.invalidate(old_checksum, only_if=path)
            self._checksum_index.put(checksum, path)

            logger.debug(f"Stored blob at {path} ({size} bytes)")
            return path

        except Exception as e:
            logger.error(f"Failed to store blob at {path}: {e}")
            raise StorageWriteError(f"Failed to write to database: {e}", "database")

    def store_stream(
        self, stream: BinaryIO, path: str, content_type: str, size: int
    ) -> str:
        """Store file from stream."""
        if size > self._max_size:
            raise StorageWriteError(
                f"File size {size} exceeds database storage limit {self._max_size}",
                "database",
            )

        # Read in chunks to enforce the size limit during the read itself,
        # rather than trusting the caller-supplied ``size`` and reading the
        # entire stream into memory unconditionally.
        chunks: list[bytes] = []
        total = 0
        chunk_size = min(64 * 1024, self._max_size)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > self._max_size:
                raise StorageWriteError(
                    f"File size {total} exceeds database storage limit {self._max_size}",
                    "database",
                )
            chunks.append(chunk)
        file_data = b"".join(chunks)
        return self.store(file_data, path, content_type)

    def retrieve(self, path: str) -> bytes:
        """Retrieve file data from database."""
        try:
            row = self._db.fetch_one(
                "SELECT content FROM media_blobs WHERE path = ?", (path,)
            )

            if not row:
                raise StorageReadError(f"File not found: {path}", "database")

            content = row["content"]
            logger.debug(f"Retrieved blob from {path} ({len(content)} bytes)")
            return content

        except StorageReadError:
            raise
        except Exception as e:
            logger.error(f"Failed to read blob at {path}: {e}")
            raise StorageReadError(f"Failed to read from database: {e}", "database")

    def retrieve_stream(self, path: str) -> Tuple[BinaryIO, int]:
        """Retrieve file as stream."""
        data = self.retrieve(path)
        return io.BytesIO(data), len(data)

    def delete(self, path: str) -> bool:
        """Delete file from database."""
        try:
            # Learn which checksum this path held so the dedupe index cannot
            # keep serving a path for a blob that no longer exists. Only drop
            # the entry when it actually points at this path; a different path
            # sharing the checksum may still be a valid dedupe target.
            row = self._db.fetch_one(
                "SELECT checksum FROM media_blobs WHERE path = ?", (path,)
            )
            old_checksum = None
            if row:
                old_checksum = row["checksum"] if isinstance(row, dict) else row[0]

            result = self._db.execute("DELETE FROM media_blobs WHERE path = ?", (path,))

            deleted = result.rowcount > 0 if hasattr(result, "rowcount") else True
            if deleted:
                if old_checksum:
                    self._checksum_index.invalidate(old_checksum, only_if=path)
                logger.debug(f"Deleted blob at {path}")
            return deleted

        except Exception as e:
            logger.error(f"Failed to delete blob at {path}: {e}")
            raise StorageDeleteError(f"Failed to delete from database: {e}", "database")

    def exists(self, path: str) -> bool:
        """Check if file exists in database."""
        row = self._db.fetch_one("SELECT 1 FROM media_blobs WHERE path = ?", (path,))
        return row is not None

    def get_url(self, path: str) -> str:
        """Get URL for file (served via API endpoint)."""
        # URL-safe base64 encode the path for the URL
        encoded_path = base64.urlsafe_b64encode(path.encode()).decode()
        return f"{self._base_url}/{encoded_path}"

    def get_size(self, path: str) -> int:
        """Get file size."""
        row = self._db.fetch_one("SELECT size FROM media_blobs WHERE path = ?", (path,))
        return row["size"] if row else 0

    def is_encrypted(self, path: str) -> bool:
        """Check if file is encrypted."""
        return False

    def get_metadata(self, path: str) -> dict:
        """Get file metadata."""
        row = self._db.fetch_one(
            """SELECT path, content_type, size, checksum, created_at, updated_at 
               FROM media_blobs WHERE path = ?""",
            (path,),
        )

        if not row:
            return {"path": path, "exists": False}

        return {
            "path": row["path"],
            "exists": True,
            "content_type": row["content_type"],
            "size": row["size"],
            "checksum": row["checksum"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_by_checksum(self, checksum: str) -> Optional[str]:
        """
        Find file path by checksum (for deduplication).

        Args:
            checksum: SHA-256 checksum

        Returns:
            Path if found, None otherwise
        """
        cached = self._checksum_index.get(checksum)
        if cached is not _MISSING:
            return cached

        row = self._db.fetch_one(
            "SELECT path FROM media_blobs WHERE checksum = ?", (checksum,)
        )
        path = row["path"] if row else None
        if path is not None:
            self._checksum_index.put(checksum, path)
        return path

    def dedupe_cache_stats(self) -> dict:
        """Snapshot of dedupe cache counters (tests / observability)."""
        return {
            "index": self._checksum_index.stats(),
            "index_size": self._checksum_index.size(),
            "index_bytes": self._checksum_index.bytes_used(),
            "memo": self._checksum_memo.stats(),
            "memo_size": self._checksum_memo.size(),
            "memo_bytes": self._checksum_memo.bytes_used(),
        }

    def cleanup_orphaned(self, valid_paths: list) -> int:
        """
        Remove blobs not in the valid paths list.

        Args:
            valid_paths: List of paths that should be kept

        Returns:
            Number of deleted blobs
        """
        if not valid_paths:
            return 0

        placeholders = ",".join("?" * len(valid_paths))
        # Only "?" placeholders are interpolated; the paths stay bound.
        template = "DELETE FROM media_blobs WHERE path NOT IN ({slots})"
        result = self._db.execute(
            template.format(slots=placeholders),
            valid_paths,
        )

        count = result.rowcount if hasattr(result, "rowcount") else 0
        if count > 0:
            # Bulk delete of arbitrary paths: cheap and correct to drop the
            # whole dedupe index rather than reverse-map every removed path.
            self._checksum_index.clear()
            logger.info(f"Cleaned up {count} orphaned blobs")
        return count

    def get_total_size(self) -> int:
        """Get total size of all stored blobs."""
        row = self._db.fetch_one("SELECT SUM(size) as total FROM media_blobs")
        return row["total"] or 0 if row else 0

    def get_count(self) -> int:
        """Get total number of stored blobs."""
        row = self._db.fetch_one("SELECT COUNT(*) as count FROM media_blobs")
        return row["count"] if row else 0
