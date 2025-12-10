"""
Media deduplication - Hash-based file deduplication and content reporting.

Provides:
- SHA-256 hashing of uploaded files
- Deduplication to avoid storing duplicate files
- Content reporting/blocklist system
- Reference counting for cleanup
"""

import hashlib
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

import utils.logger as logger
import utils.config as config


class HashAlgorithm(Enum):
    """Supported hash algorithms."""
    SHA256 = "sha256"
    SHA512 = "sha512"
    BLAKE2B = "blake2b"


class ReportStatus(Enum):
    """Status of a reported hash."""
    PENDING = "pending"
    REVIEWED = "reviewed"
    BLOCKED = "blocked"
    CLEARED = "cleared"


@dataclass
class FileHash:
    """Represents a file hash record."""
    id: int
    hash_value: str
    algorithm: str
    file_size: int
    content_type: str
    reference_count: int
    first_seen: int
    storage_path: Optional[str] = None
    storage_backend: Optional[str] = None


@dataclass
class HashReport:
    """Represents a content report for a hash."""
    id: int
    hash_value: str
    reporter_id: int
    reporter_username: Optional[str]
    reason: str
    details: Optional[str]
    status: ReportStatus
    reported_at: int
    reviewed_at: Optional[int]
    reviewed_by: Optional[int]
    admin_notes: Optional[str]


@dataclass
class DeduplicationResult:
    """Result of deduplication check."""
    is_duplicate: bool
    hash_value: str
    existing_file_id: Optional[int] = None
    existing_url: Optional[str] = None
    is_blocked: bool = False
    block_reason: Optional[str] = None


SCHEMA = """
-- File hashes for deduplication
CREATE TABLE IF NOT EXISTS media_file_hashes (
    id INTEGER PRIMARY KEY,
    hash_value TEXT NOT NULL UNIQUE,
    algorithm TEXT NOT NULL DEFAULT 'sha256',
    file_size INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    reference_count INTEGER NOT NULL DEFAULT 1,
    first_seen INTEGER NOT NULL,
    storage_path TEXT,
    storage_backend TEXT
);

-- Hash reports for content moderation
CREATE TABLE IF NOT EXISTS media_hash_reports (
    id INTEGER PRIMARY KEY,
    hash_value TEXT NOT NULL,
    reporter_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reported_at INTEGER NOT NULL,
    reviewed_at INTEGER,
    reviewed_by INTEGER,
    admin_notes TEXT,
    FOREIGN KEY (reporter_id) REFERENCES auth_users(id)
);

-- Blocked hashes
CREATE TABLE IF NOT EXISTS media_blocked_hashes (
    hash_value TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_at INTEGER NOT NULL,
    blocked_by INTEGER,
    auto_blocked INTEGER DEFAULT 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_media_file_hashes_hash ON media_file_hashes(hash_value);
CREATE INDEX IF NOT EXISTS idx_media_hash_reports_hash ON media_hash_reports(hash_value);
CREATE INDEX IF NOT EXISTS idx_media_hash_reports_status ON media_hash_reports(status);
CREATE INDEX IF NOT EXISTS idx_media_hash_reports_reporter ON media_hash_reports(reporter_id);
"""


class DeduplicationManager:
    """Manages file deduplication and content reporting."""
    
    def __init__(self, db):
        """Initialize deduplication manager."""
        self._db = db
        self._config = self._load_config()
        self._create_tables()
    
    def _load_config(self) -> dict:
        """Load deduplication configuration."""
        media_config = config.get("media", {})
        dedup_config = media_config.get("deduplication", {})
        
        return {
            "enabled": dedup_config.get("enabled", True),
            "hash_algorithm": dedup_config.get("hash_algorithm", "sha256"),
            "min_size": dedup_config.get("min_size", 10240),  # 10KB minimum
            "auto_block_threshold": dedup_config.get("auto_block_threshold", 5),  # Auto-block after 5 reports
        }
    
    def _create_tables(self):
        """Create deduplication tables."""
        statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
        for statement in statements:
            if statement:
                try:
                    converted = self._db.convert_schema(statement) if hasattr(self._db, 'convert_schema') else statement
                    self._db.execute(converted)
                except Exception as e:
                    logger.error(f"Failed to create deduplication table: {e}")
    
    def compute_hash(self, file_data: bytes) -> str:
        """Compute hash of file data."""
        algorithm = self._config["hash_algorithm"]
        
        if algorithm == "sha512":
            return hashlib.sha512(file_data).hexdigest()
        elif algorithm == "blake2b":
            return hashlib.blake2b(file_data).hexdigest()
        else:
            return hashlib.sha256(file_data).hexdigest()
    
    def is_blocked(self, hash_value: str) -> Tuple[bool, Optional[str]]:
        """Check if a hash is blocked."""
        row = self._db.fetch_one(
            "SELECT reason FROM media_blocked_hashes WHERE hash_value = ?",
            (hash_value,)
        )
        if row:
            reason = row["reason"] if isinstance(row, dict) else row[0]
            return True, reason
        return False, None
    
    def check_duplicate(
        self,
        file_data: bytes,
        content_type: str
    ) -> DeduplicationResult:
        """
        Check if file is a duplicate and if it's blocked.
        
        Args:
            file_data: Raw file bytes
            content_type: MIME type
            
        Returns:
            DeduplicationResult with duplicate/block status
        """
        if not self._config["enabled"]:
            return DeduplicationResult(
                is_duplicate=False,
                hash_value=self.compute_hash(file_data)
            )
        
        file_size = len(file_data)
        
        # Skip deduplication for small files
        if file_size < self._config["min_size"]:
            return DeduplicationResult(
                is_duplicate=False,
                hash_value=self.compute_hash(file_data)
            )
        
        hash_value = self.compute_hash(file_data)
        
        # Check if blocked
        is_blocked, block_reason = self.is_blocked(hash_value)
        if is_blocked:
            return DeduplicationResult(
                is_duplicate=False,
                hash_value=hash_value,
                is_blocked=True,
                block_reason=block_reason
            )
        
        # Check for existing file with same hash
        row = self._db.fetch_one(
            """SELECT id, storage_path, storage_backend 
               FROM media_file_hashes WHERE hash_value = ?""",
            (hash_value,)
        )
        
        if row:
            if isinstance(row, dict):
                file_id = row["id"]
                storage_path = row["storage_path"]
            else:
                file_id, storage_path, _ = row
            
            return DeduplicationResult(
                is_duplicate=True,
                hash_value=hash_value,
                existing_file_id=file_id,
                existing_url=storage_path
            )
        
        return DeduplicationResult(
            is_duplicate=False,
            hash_value=hash_value
        )
    
    def register_file(
        self,
        hash_value: str,
        file_size: int,
        content_type: str,
        storage_path: str,
        storage_backend: str,
        timestamp: int
    ) -> int:
        """
        Register a new file hash or increment reference count.
        
        Returns:
            Hash record ID
        """
        if not self._config["enabled"]:
            return 0
        
        # Check if hash already exists
        row = self._db.fetch_one(
            "SELECT id, reference_count FROM media_file_hashes WHERE hash_value = ?",
            (hash_value,)
        )
        
        if row:
            hash_id = row["id"] if isinstance(row, dict) else row[0]
            # Increment reference count
            self._db.execute(
                "UPDATE media_file_hashes SET reference_count = reference_count + 1 WHERE id = ?",
                (hash_id,)
            )
            return hash_id
        
        # Create new hash record
        from src.utils.encryption import generate_snowflake_id
        hash_id = generate_snowflake_id()
        
        self._db.execute(
            """INSERT INTO media_file_hashes 
               (id, hash_value, algorithm, file_size, content_type, reference_count, 
                first_seen, storage_path, storage_backend)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (hash_id, hash_value, self._config["hash_algorithm"], file_size,
             content_type, timestamp, storage_path, storage_backend)
        )
        
        return hash_id
    
    def decrement_reference(self, hash_value: str) -> bool:
        """
        Decrement reference count for a hash.
        
        Returns:
            True if file can be deleted (reference count is 0)
        """
        if not self._config["enabled"]:
            return True
        
        row = self._db.fetch_one(
            "SELECT id, reference_count FROM media_file_hashes WHERE hash_value = ?",
            (hash_value,)
        )
        
        if not row:
            return True
        
        ref_count = row["reference_count"] if isinstance(row, dict) else row[1]
        hash_id = row["id"] if isinstance(row, dict) else row[0]
        
        if ref_count <= 1:
            # Delete hash record
            self._db.execute("DELETE FROM media_file_hashes WHERE id = ?", (hash_id,))
            return True
        else:
            # Decrement count
            self._db.execute(
                "UPDATE media_file_hashes SET reference_count = reference_count - 1 WHERE id = ?",
                (hash_id,)
            )
            return False
    
    def report_hash(
        self,
        hash_value: str,
        reporter_id: int,
        reason: str,
        details: Optional[str] = None
    ) -> int:
        """
        Report a file hash for content moderation.
        
        Returns:
            Report ID
        """
        from src.utils.encryption import generate_snowflake_id
        import time
        
        report_id = generate_snowflake_id()
        now = int(time.time() * 1000)
        
        self._db.execute(
            """INSERT INTO media_hash_reports 
               (id, hash_value, reporter_id, reason, details, status, reported_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (report_id, hash_value, reporter_id, reason, details, now)
        )
        
        # Check if auto-block threshold reached
        report_count = self._get_report_count(hash_value)
        if report_count >= self._config["auto_block_threshold"]:
            self.block_hash(hash_value, f"Auto-blocked: {report_count} reports", auto=True)
        
        logger.info(f"Hash {hash_value[:16]}... reported by user {reporter_id}: {reason}")
        return report_id
    
    def _get_report_count(self, hash_value: str) -> int:
        """Get number of reports for a hash."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM media_hash_reports WHERE hash_value = ? AND status = 'pending'",
            (hash_value,)
        )
        return row["count"] if isinstance(row, dict) else row[0] if row else 0
    
    def block_hash(
        self,
        hash_value: str,
        reason: str,
        blocked_by: Optional[int] = None,
        auto: bool = False
    ) -> bool:
        """Block a hash from being uploaded."""
        import time
        now = int(time.time() * 1000)
        
        try:
            self._db.execute(
                """INSERT OR REPLACE INTO media_blocked_hashes 
                   (hash_value, reason, blocked_at, blocked_by, auto_blocked)
                   VALUES (?, ?, ?, ?, ?)""",
                (hash_value, reason, now, blocked_by, 1 if auto else 0)
            )
            
            # Update all pending reports for this hash
            self._db.execute(
                """UPDATE media_hash_reports 
                   SET status = 'blocked', reviewed_at = ?, reviewed_by = ?
                   WHERE hash_value = ? AND status = 'pending'""",
                (now, blocked_by, hash_value)
            )
            
            logger.info(f"Hash {hash_value[:16]}... blocked: {reason}")
            return True
        except Exception as e:
            logger.error(f"Failed to block hash: {e}")
            return False
    
    def unblock_hash(self, hash_value: str) -> bool:
        """Unblock a hash."""
        try:
            self._db.execute(
                "DELETE FROM media_blocked_hashes WHERE hash_value = ?",
                (hash_value,)
            )
            logger.info(f"Hash {hash_value[:16]}... unblocked")
            return True
        except Exception as e:
            logger.error(f"Failed to unblock hash: {e}")
            return False
    
    def get_reports(
        self,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[HashReport]:
        """Get hash reports for admin review."""
        if status_filter:
            rows = self._db.fetch_all(
                """SELECT r.id, r.hash_value, r.reporter_id, u.username, r.reason, 
                          r.details, r.status, r.reported_at, r.reviewed_at, 
                          r.reviewed_by, r.admin_notes
                   FROM media_hash_reports r
                   LEFT JOIN auth_users u ON r.reporter_id = u.id
                   WHERE r.status = ?
                   ORDER BY r.reported_at DESC
                   LIMIT ? OFFSET ?""",
                (status_filter, limit, offset)
            )
        else:
            rows = self._db.fetch_all(
                """SELECT r.id, r.hash_value, r.reporter_id, u.username, r.reason, 
                          r.details, r.status, r.reported_at, r.reviewed_at, 
                          r.reviewed_by, r.admin_notes
                   FROM media_hash_reports r
                   LEFT JOIN auth_users u ON r.reporter_id = u.id
                   ORDER BY r.reported_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset)
            )
        
        reports = []
        for row in rows:
            if isinstance(row, dict):
                reports.append(HashReport(
                    id=row["id"],
                    hash_value=row["hash_value"],
                    reporter_id=row["reporter_id"],
                    reporter_username=row["username"],
                    reason=row["reason"],
                    details=row["details"],
                    status=ReportStatus(row["status"]),
                    reported_at=row["reported_at"],
                    reviewed_at=row["reviewed_at"],
                    reviewed_by=row["reviewed_by"],
                    admin_notes=row["admin_notes"]
                ))
            else:
                reports.append(HashReport(
                    id=row[0], hash_value=row[1], reporter_id=row[2],
                    reporter_username=row[3], reason=row[4], details=row[5],
                    status=ReportStatus(row[6]), reported_at=row[7],
                    reviewed_at=row[8], reviewed_by=row[9], admin_notes=row[10]
                ))
        
        return reports
    
    def get_report_counts(self) -> Dict[str, int]:
        """Get counts of reports by status."""
        counts = {"pending": 0, "reviewed": 0, "blocked": 0, "cleared": 0, "total": 0}
        
        rows = self._db.fetch_all(
            "SELECT status, COUNT(*) as count FROM media_hash_reports GROUP BY status"
        )
        
        for row in rows:
            status = row["status"] if isinstance(row, dict) else row[0]
            count = row["count"] if isinstance(row, dict) else row[1]
            if status in counts:
                counts[status] = count
            counts["total"] += count
        
        return counts
    
    def get_blocked_hashes(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get list of blocked hashes."""
        rows = self._db.fetch_all(
            """SELECT hash_value, reason, blocked_at, blocked_by, auto_blocked
               FROM media_blocked_hashes
               ORDER BY blocked_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )
        
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append({
                    "hash_value": row["hash_value"],
                    "reason": row["reason"],
                    "blocked_at": row["blocked_at"],
                    "blocked_by": row["blocked_by"],
                    "auto_blocked": bool(row["auto_blocked"])
                })
            else:
                result.append({
                    "hash_value": row[0],
                    "reason": row[1],
                    "blocked_at": row[2],
                    "blocked_by": row[3],
                    "auto_blocked": bool(row[4])
                })
        
        return result
    
    def review_report(
        self,
        report_id: int,
        admin_id: int,
        action: str,
        notes: Optional[str] = None
    ) -> bool:
        """
        Review a hash report.
        
        Args:
            report_id: Report ID
            admin_id: Admin user ID
            action: 'block', 'clear', or 'dismiss'
            notes: Admin notes
        """
        import time
        now = int(time.time() * 1000)
        
        # Get report
        row = self._db.fetch_one(
            "SELECT hash_value FROM media_hash_reports WHERE id = ?",
            (report_id,)
        )
        if not row:
            return False
        
        hash_value = row["hash_value"] if isinstance(row, dict) else row[0]
        
        if action == "block":
            self.block_hash(hash_value, notes or "Blocked by admin", admin_id)
            status = "blocked"
        elif action == "clear":
            status = "cleared"
        else:
            status = "reviewed"
        
        self._db.execute(
            """UPDATE media_hash_reports 
               SET status = ?, reviewed_at = ?, reviewed_by = ?, admin_notes = ?
               WHERE id = ?""",
            (status, now, admin_id, notes, report_id)
        )
        
        return True
