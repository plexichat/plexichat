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
from typing import BinaryIO, Tuple, Optional

import utils.logger as logger

from .base import StorageBackendBase
from ..exceptions import (
    StorageError,
    StorageWriteError,
    StorageReadError,
    StorageDeleteError,
)


# Default max size: 512KB - good for text files, small docs
DEFAULT_MAX_SIZE = 512 * 1024


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
        converted = self._db.convert_schema(schema) if hasattr(self._db, 'convert_schema') else schema
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
        """Compute SHA-256 checksum."""
        return hashlib.sha256(data).hexdigest()
    
    def store(self, file_data: bytes, path: str, content_type: str) -> str:
        """Store file data in database."""
        size = len(file_data)
        
        if size > self._max_size:
            raise StorageWriteError(
                f"File size {size} exceeds database storage limit {self._max_size}. "
                f"Use local or S3 storage for larger files.",
                "database"
            )
        
        checksum = self._compute_checksum(file_data)
        now = self._get_timestamp()
        
        try:
            # Check if path exists (update) or new (insert)
            existing = self._db.fetch_one(
                "SELECT path FROM media_blobs WHERE path = ?",
                (path,)
            )
            
            if existing:
                self._db.execute(
                    """UPDATE media_blobs 
                       SET content = ?, content_type = ?, size = ?, 
                           checksum = ?, updated_at = ?
                       WHERE path = ?""",
                    (file_data, content_type, size, checksum, now, path)
                )
            else:
                self._db.execute(
                    """INSERT INTO media_blobs 
                       (path, content, content_type, size, checksum, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (path, file_data, content_type, size, checksum, now, now)
                )
            
            logger.debug(f"Stored blob at {path} ({size} bytes)")
            return path
            
        except Exception as e:
            logger.error(f"Failed to store blob at {path}: {e}")
            raise StorageWriteError(f"Failed to write to database: {e}", "database")
    
    def store_stream(self, stream: BinaryIO, path: str, content_type: str, size: int) -> str:
        """Store file from stream."""
        if size > self._max_size:
            raise StorageWriteError(
                f"File size {size} exceeds database storage limit {self._max_size}",
                "database"
            )
        
        # Read entire stream into memory (acceptable for small files)
        file_data = stream.read()
        return self.store(file_data, path, content_type)
    
    def retrieve(self, path: str) -> bytes:
        """Retrieve file data from database."""
        try:
            row = self._db.fetch_one(
                "SELECT content FROM media_blobs WHERE path = ?",
                (path,)
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
            result = self._db.execute(
                "DELETE FROM media_blobs WHERE path = ?",
                (path,)
            )
            
            deleted = result.rowcount > 0 if hasattr(result, 'rowcount') else True
            if deleted:
                logger.debug(f"Deleted blob at {path}")
            return deleted
            
        except Exception as e:
            logger.error(f"Failed to delete blob at {path}: {e}")
            raise StorageDeleteError(f"Failed to delete from database: {e}", "database")
    
    def exists(self, path: str) -> bool:
        """Check if file exists in database."""
        row = self._db.fetch_one(
            "SELECT 1 FROM media_blobs WHERE path = ?",
            (path,)
        )
        return row is not None
    
    def get_url(self, path: str) -> str:
        """Get URL for file (served via API endpoint)."""
        # URL-safe base64 encode the path for the URL
        encoded_path = base64.urlsafe_b64encode(path.encode()).decode()
        return f"{self._base_url}/{encoded_path}"
    
    def get_size(self, path: str) -> int:
        """Get file size."""
        row = self._db.fetch_one(
            "SELECT size FROM media_blobs WHERE path = ?",
            (path,)
        )
        return row["size"] if row else 0
    
    def get_metadata(self, path: str) -> dict:
        """Get file metadata."""
        row = self._db.fetch_one(
            """SELECT path, content_type, size, checksum, created_at, updated_at 
               FROM media_blobs WHERE path = ?""",
            (path,)
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
        row = self._db.fetch_one(
            "SELECT path FROM media_blobs WHERE checksum = ?",
            (checksum,)
        )
        return row["path"] if row else None
    
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
        result = self._db.execute(
            f"DELETE FROM media_blobs WHERE path NOT IN ({placeholders})",
            valid_paths
        )
        
        count = result.rowcount if hasattr(result, 'rowcount') else 0
        if count > 0:
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
