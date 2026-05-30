"""
Constants for media deduplication - enums, dataclasses, schema, and setup functions.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import utils.logger as logger
import utils.config as config


class HashAlgorithm(Enum):
    """Supported hash algorithms."""

    SHA256 = "sha256"
    SHA512 = "sha512"
    BLAKE2B = "blake2b"
    PHASH = "phash"


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
    phash_value TEXT,
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
    phash_value TEXT,
    reporter_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reported_at INTEGER NOT NULL,
    reviewed_at INTEGER,
    reviewed_by INTEGER,
    admin_notes TEXT,
    uploader_id INTEGER,
    message_id INTEGER,
    attachment_url TEXT,
    block_uploader INTEGER DEFAULT 0,
    FOREIGN KEY (reporter_id) REFERENCES auth_users(id)
);

-- Blocked hashes (supports both SHA256 and pHash)
CREATE TABLE IF NOT EXISTS media_blocked_hashes (
    hash_value TEXT PRIMARY KEY,
    hash_type TEXT NOT NULL DEFAULT 'sha256',
    phash_threshold INTEGER DEFAULT 10,
    reason TEXT NOT NULL,
    blocked_at INTEGER NOT NULL,
    blocked_by INTEGER,
    auto_blocked INTEGER DEFAULT 0
);

-- Blocked users (banned from uploading)
CREATE TABLE IF NOT EXISTS media_blocked_users (
    user_id INTEGER PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_at INTEGER NOT NULL,
    blocked_by INTEGER,
    expires_at INTEGER
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_media_file_hashes_hash ON media_file_hashes(hash_value);
CREATE INDEX IF NOT EXISTS idx_media_file_hashes_phash ON media_file_hashes(phash_value);
CREATE INDEX IF NOT EXISTS idx_media_hash_reports_hash ON media_hash_reports(hash_value);
CREATE INDEX IF NOT EXISTS idx_media_hash_reports_phash ON media_hash_reports(phash_value);
CREATE INDEX IF NOT EXISTS idx_media_hash_reports_status ON media_hash_reports(status);
CREATE INDEX IF NOT EXISTS idx_media_hash_reports_reporter ON media_hash_reports(reporter_id);
CREATE INDEX IF NOT EXISTS idx_media_blocked_hashes_type ON media_blocked_hashes(hash_type);
"""


def setup(db):
    """Initialize the deduplication module."""
    return None


def create_tables(db):
    """Create deduplication tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    for statement in statements:
        if statement:
            try:
                converted = (
                    db.convert_schema(statement)
                    if hasattr(db, "convert_schema")
                    else statement
                )
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to create deduplication table: {e}")
