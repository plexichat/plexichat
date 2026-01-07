"""
Chunked uploads - Support for large file uploads with resumability.

Provides:
- Chunked upload sessions
- Resumable uploads
- Progress tracking
- Session cleanup
"""

import os
import time
import hashlib
import tempfile
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum

import utils.logger as logger
import utils.config as config


class UploadSessionStatus(Enum):
    """Status of an upload session."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class UploadSession:
    """Represents a chunked upload session."""

    id: str
    user_id: int
    filename: str
    content_type: str
    total_size: int
    chunk_size: int
    total_chunks: int
    uploaded_chunks: int
    uploaded_bytes: int
    status: UploadSessionStatus
    created_at: int
    updated_at: int
    expires_at: int
    temp_path: Optional[str] = None
    checksum: Optional[str] = None


@dataclass
class ChunkUploadResult:
    """Result of uploading a chunk."""

    success: bool
    session_id: str
    chunk_index: int
    uploaded_chunks: int
    total_chunks: int
    progress_percent: float
    is_complete: bool
    error: Optional[str] = None


SCHEMA = """
-- Chunked upload sessions
CREATE TABLE IF NOT EXISTS media_upload_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    total_size INTEGER NOT NULL,
    chunk_size INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    uploaded_chunks INTEGER NOT NULL DEFAULT 0,
    uploaded_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    temp_path TEXT,
    checksum TEXT,
    chunk_checksums TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_upload_sessions_user ON media_upload_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_upload_sessions_status ON media_upload_sessions(status);
CREATE INDEX IF NOT EXISTS idx_upload_sessions_expires ON media_upload_sessions(expires_at);
"""


class ChunkedUploadManager:
    """Manages chunked file uploads."""

    def __init__(self, db):
        """Initialize chunked upload manager."""
        self._db = db
        self._config = self._load_config()
        self._create_tables()
        self._temp_dir = self._ensure_temp_dir()

    def _load_config(self) -> dict:
        """Load chunked upload configuration."""
        media_config = config.get("media", {})
        chunked_config = media_config.get("chunked_upload", {})

        return {
            "enabled": chunked_config.get("enabled", True),
            "chunk_size": chunked_config.get("chunk_size", 5 * 1024 * 1024),  # 5MB
            "max_chunks": chunked_config.get("max_chunks", 200),
            "session_timeout": chunked_config.get("session_timeout", 3600),  # 1 hour
            "cleanup_interval": chunked_config.get(
                "cleanup_interval", 300
            ),  # 5 minutes
        }

    def _create_tables(self):
        """Create chunked upload tables."""
        statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
        for statement in statements:
            if statement:
                try:
                    converted = (
                        self._db.convert_schema(statement)
                        if hasattr(self._db, "convert_schema")
                        else statement
                    )
                    self._db.execute(converted)
                except Exception as e:
                    logger.error(f"Failed to create chunked upload table: {e}")

    def _ensure_temp_dir(self) -> str:
        """Ensure temp directory exists."""
        storage_config = config.get("storage", {})
        temp_dir = storage_config.get("temp_dir", tempfile.gettempdir())
        upload_temp = os.path.join(temp_dir, "chunked_uploads")
        os.makedirs(upload_temp, exist_ok=True)
        return upload_temp

    def is_enabled(self) -> bool:
        """Check if chunked uploads are enabled."""
        return self._config["enabled"]

    def create_session(
        self, user_id: int, filename: str, content_type: str, total_size: int
    ) -> Optional[UploadSession]:
        """
        Create a new upload session.

        Args:
            user_id: User ID
            filename: Original filename
            content_type: MIME type
            total_size: Total file size in bytes

        Returns:
            UploadSession or None if failed
        """
        if not self.is_enabled():
            return None

        chunk_size = self._config["chunk_size"]
        total_chunks = (total_size + chunk_size - 1) // chunk_size

        if total_chunks > self._config["max_chunks"]:
            logger.warning(f"File too large for chunked upload: {total_chunks} chunks")
            return None

        import secrets

        session_id = secrets.token_urlsafe(32)

        now = int(time.time() * 1000)
        expires_at = now + (self._config["session_timeout"] * 1000)

        # Create temp file
        temp_path = os.path.join(self._temp_dir, f"{session_id}.tmp")

        # Pre-allocate file
        try:
            with open(temp_path, "wb") as f:
                f.seek(total_size - 1)
                f.write(b"\0")
        except Exception as e:
            logger.error(f"Failed to create temp file: {e}")
            return None

        self._db.execute(
            """INSERT INTO media_upload_sessions 
               (id, user_id, filename, content_type, total_size, chunk_size, 
                total_chunks, uploaded_chunks, uploaded_bytes, status, 
                created_at, updated_at, expires_at, temp_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'pending', ?, ?, ?, ?)""",
            (
                session_id,
                user_id,
                filename,
                content_type,
                total_size,
                chunk_size,
                total_chunks,
                now,
                now,
                expires_at,
                temp_path,
            ),
        )

        logger.debug(
            f"Created upload session {session_id} for {filename} ({total_chunks} chunks)"
        )

        return UploadSession(
            id=session_id,
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            total_size=total_size,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            uploaded_chunks=0,
            uploaded_bytes=0,
            status=UploadSessionStatus.PENDING,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            temp_path=temp_path,
        )

    def get_session(self, session_id: str, user_id: int) -> Optional[UploadSession]:
        """Get an upload session."""
        row = self._db.fetch_one(
            """SELECT id, user_id, filename, content_type, total_size, chunk_size,
                      total_chunks, uploaded_chunks, uploaded_bytes, status,
                      created_at, updated_at, expires_at, temp_path, checksum
               FROM media_upload_sessions
               WHERE id = ? AND user_id = ?""",
            (session_id, user_id),
        )

        if not row:
            return None

        if isinstance(row, dict):
            return UploadSession(
                id=row["id"],
                user_id=row["user_id"],
                filename=row["filename"],
                content_type=row["content_type"],
                total_size=row["total_size"],
                chunk_size=row["chunk_size"],
                total_chunks=row["total_chunks"],
                uploaded_chunks=row["uploaded_chunks"],
                uploaded_bytes=row["uploaded_bytes"],
                status=UploadSessionStatus(row["status"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                expires_at=row["expires_at"],
                temp_path=row["temp_path"],
                checksum=row["checksum"],
            )
        else:
            return UploadSession(
                id=row[0],
                user_id=row[1],
                filename=row[2],
                content_type=row[3],
                total_size=row[4],
                chunk_size=row[5],
                total_chunks=row[6],
                uploaded_chunks=row[7],
                uploaded_bytes=row[8],
                status=UploadSessionStatus(row[9]),
                created_at=row[10],
                updated_at=row[11],
                expires_at=row[12],
                temp_path=row[13],
                checksum=row[14],
            )

    def upload_chunk(
        self,
        session_id: str,
        user_id: int,
        chunk_index: int,
        chunk_data: bytes,
        chunk_checksum: Optional[str] = None,
    ) -> ChunkUploadResult:
        """
        Upload a chunk to a session.

        Args:
            session_id: Session ID
            user_id: User ID
            chunk_index: Zero-based chunk index
            chunk_data: Chunk bytes
            chunk_checksum: Optional MD5 checksum for verification

        Returns:
            ChunkUploadResult
        """
        session = self.get_session(session_id, user_id)

        if not session:
            return ChunkUploadResult(
                success=False,
                session_id=session_id,
                chunk_index=chunk_index,
                uploaded_chunks=0,
                total_chunks=0,
                progress_percent=0,
                is_complete=False,
                error="Session not found",
            )

        # Check if session expired
        now = int(time.time() * 1000)
        if now > session.expires_at:
            self._expire_session(session_id)
            return ChunkUploadResult(
                success=False,
                session_id=session_id,
                chunk_index=chunk_index,
                uploaded_chunks=session.uploaded_chunks,
                total_chunks=session.total_chunks,
                progress_percent=0,
                is_complete=False,
                error="Session expired",
            )

        # Validate chunk index
        if chunk_index < 0 or chunk_index >= session.total_chunks:
            return ChunkUploadResult(
                success=False,
                session_id=session_id,
                chunk_index=chunk_index,
                uploaded_chunks=session.uploaded_chunks,
                total_chunks=session.total_chunks,
                progress_percent=(session.uploaded_chunks / session.total_chunks) * 100,
                is_complete=False,
                error=f"Invalid chunk index: {chunk_index}",
            )

        # Verify checksum if provided
        if chunk_checksum:
            actual_checksum = hashlib.md5(chunk_data).hexdigest()
            if actual_checksum != chunk_checksum:
                return ChunkUploadResult(
                    success=False,
                    session_id=session_id,
                    chunk_index=chunk_index,
                    uploaded_chunks=session.uploaded_chunks,
                    total_chunks=session.total_chunks,
                    progress_percent=(session.uploaded_chunks / session.total_chunks)
                    * 100,
                    is_complete=False,
                    error="Checksum mismatch",
                )

        # Write chunk to temp file
        try:
            offset = chunk_index * session.chunk_size
            if session.temp_path is None:
                raise RuntimeError("Upload session temp file missing")
            with open(session.temp_path, "r+b") as f:
                f.seek(offset)
                f.write(chunk_data)
        except Exception as e:
            logger.error(f"Failed to write chunk: {e}")
            return ChunkUploadResult(
                success=False,
                session_id=session_id,
                chunk_index=chunk_index,
                uploaded_chunks=session.uploaded_chunks,
                total_chunks=session.total_chunks,
                progress_percent=(session.uploaded_chunks / session.total_chunks) * 100,
                is_complete=False,
                error=f"Write failed: {e}",
            )

        # Update session
        uploaded_chunks = session.uploaded_chunks + 1
        uploaded_bytes = session.uploaded_bytes + len(chunk_data)
        is_complete = uploaded_chunks >= session.total_chunks
        status = "completed" if is_complete else "in_progress"

        self._db.execute(
            """UPDATE media_upload_sessions 
               SET uploaded_chunks = ?, uploaded_bytes = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (uploaded_chunks, uploaded_bytes, status, now, session_id),
        )

        progress = (uploaded_chunks / session.total_chunks) * 100

        logger.debug(
            f"Chunk {chunk_index + 1}/{session.total_chunks} uploaded for session {session_id} ({progress:.1f}%)"
        )

        return ChunkUploadResult(
            success=True,
            session_id=session_id,
            chunk_index=chunk_index,
            uploaded_chunks=uploaded_chunks,
            total_chunks=session.total_chunks,
            progress_percent=progress,
            is_complete=is_complete,
        )

    def complete_session(self, session_id: str, user_id: int) -> Optional[bytes]:
        """
        Complete an upload session and return the assembled file.

        Args:
            session_id: Session ID
            user_id: User ID

        Returns:
            Complete file bytes or None if failed
        """
        session = self.get_session(session_id, user_id)

        if not session:
            return None

        if session.status != UploadSessionStatus.COMPLETED:
            logger.warning(f"Session {session_id} not complete: {session.status}")
            return None

        try:
            if session.temp_path is None:
                raise RuntimeError("Upload session temp file missing")
            with open(session.temp_path, "rb") as f:
                file_data = f.read(session.total_size)

            # Cleanup
            self._cleanup_session(session_id)

            return file_data
        except Exception as e:
            logger.error(f"Failed to read completed upload: {e}")
            return None

    def cancel_session(self, session_id: str, user_id: int) -> bool:
        """Cancel an upload session."""
        session = self.get_session(session_id, user_id)
        if not session:
            return False

        self._cleanup_session(session_id)
        return True

    def _expire_session(self, session_id: str):
        """Mark session as expired."""
        self._db.execute(
            "UPDATE media_upload_sessions SET status = 'expired' WHERE id = ?",
            (session_id,),
        )

    def _cleanup_session(self, session_id: str):
        """Clean up session and temp files."""
        row = self._db.fetch_one(
            "SELECT temp_path FROM media_upload_sessions WHERE id = ?", (session_id,)
        )

        if row:
            temp_path = row["temp_path"] if isinstance(row, dict) else row[0]
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to delete temp file: {e}")

        self._db.execute(
            "DELETE FROM media_upload_sessions WHERE id = ?", (session_id,)
        )

    def cleanup_expired(self) -> int:
        """Clean up expired sessions. Returns count of cleaned sessions."""
        now = int(time.time() * 1000)

        rows = self._db.fetch_all(
            "SELECT id, temp_path FROM media_upload_sessions WHERE expires_at < ?",
            (now,),
        )

        count = 0
        for row in rows:
            session_id = row["id"] if isinstance(row, dict) else row[0]
            temp_path = row["temp_path"] if isinstance(row, dict) else row[1]

            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

            self._db.execute(
                "DELETE FROM media_upload_sessions WHERE id = ?", (session_id,)
            )
            count += 1

        if count > 0:
            logger.info(f"Cleaned up {count} expired upload sessions")

        return count

    def get_user_sessions(self, user_id: int) -> List[UploadSession]:
        """Get all active sessions for a user."""
        now = int(time.time() * 1000)

        rows = self._db.fetch_all(
            """SELECT id, user_id, filename, content_type, total_size, chunk_size,
                      total_chunks, uploaded_chunks, uploaded_bytes, status,
                      created_at, updated_at, expires_at, temp_path, checksum
               FROM media_upload_sessions
               WHERE user_id = ? AND expires_at > ? AND status NOT IN ('completed', 'expired')
               ORDER BY created_at DESC""",
            (user_id, now),
        )

        sessions = []
        for row in rows:
            if isinstance(row, dict):
                sessions.append(
                    UploadSession(
                        id=row["id"],
                        user_id=row["user_id"],
                        filename=row["filename"],
                        content_type=row["content_type"],
                        total_size=row["total_size"],
                        chunk_size=row["chunk_size"],
                        total_chunks=row["total_chunks"],
                        uploaded_chunks=row["uploaded_chunks"],
                        uploaded_bytes=row["uploaded_bytes"],
                        status=UploadSessionStatus(row["status"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        expires_at=row["expires_at"],
                        temp_path=row["temp_path"],
                        checksum=row["checksum"],
                    )
                )

        return sessions
