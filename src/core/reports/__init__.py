"""
Reports Module - Message and user reporting for content moderation.

Provides:
- Message reporting (text content, not just attachments)
- User behavior reporting
- Admin review system
- Auto-action thresholds

Usage:
    from src.core import reports
    reports.setup(db)
    
    # Report a message
    report = reports.report_message(reporter_id, message_id, reason, details)
    
    # Report a user
    report = reports.report_user(reporter_id, user_id, reason, details)
"""

import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

import utils.logger as logger
import utils.config as config

from src.utils.encryption import generate_snowflake_id


class ReportStatus(Enum):
    """Status of a report."""
    PENDING = "pending"
    REVIEWED = "reviewed"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


class ReportCategory(Enum):
    """Category of report."""
    HARASSMENT = "harassment"
    SPAM = "spam"
    INAPPROPRIATE = "inappropriate"
    ILLEGAL = "illegal"
    HATE_SPEECH = "hate_speech"
    THREATS = "threats"
    IMPERSONATION = "impersonation"
    OTHER = "other"


@dataclass
class MessageReport:
    """Represents a message report."""
    id: int
    message_id: int
    channel_id: int
    server_id: Optional[int]
    reporter_id: int
    reported_user_id: int
    reason: str
    category: str
    details: Optional[str]
    message_content: Optional[str]
    status: ReportStatus
    reported_at: int
    reviewed_at: Optional[int]
    reviewed_by: Optional[int]
    admin_notes: Optional[str]
    action_taken: Optional[str]


@dataclass
class UserReport:
    """Represents a user behavior report."""
    id: int
    reported_user_id: int
    reporter_id: int
    reason: str
    category: str
    details: Optional[str]
    evidence_message_ids: Optional[List[int]]
    status: ReportStatus
    reported_at: int
    reviewed_at: Optional[int]
    reviewed_by: Optional[int]
    admin_notes: Optional[str]
    action_taken: Optional[str]


SCHEMA = """
-- Message reports
CREATE TABLE IF NOT EXISTS message_reports (
    id BIGINT PRIMARY KEY,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    server_id BIGINT,
    reporter_id BIGINT NOT NULL,
    reported_user_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    details TEXT,
    message_content TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reported_at BIGINT NOT NULL,
    reviewed_at BIGINT,
    reviewed_by BIGINT,
    admin_notes TEXT,
    action_taken TEXT
);

-- User reports
CREATE TABLE IF NOT EXISTS user_reports (
    id BIGINT PRIMARY KEY,
    reported_user_id BIGINT NOT NULL,
    reporter_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    details TEXT,
    evidence_message_ids TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reported_at BIGINT NOT NULL,
    reviewed_at BIGINT,
    reviewed_by BIGINT,
    admin_notes TEXT,
    action_taken TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_message_reports_status ON message_reports(status);
CREATE INDEX IF NOT EXISTS idx_message_reports_reporter ON message_reports(reporter_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_reported ON message_reports(reported_user_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_message ON message_reports(message_id);
CREATE INDEX IF NOT EXISTS idx_user_reports_status ON user_reports(status);
CREATE INDEX IF NOT EXISTS idx_user_reports_reporter ON user_reports(reporter_id);
CREATE INDEX IF NOT EXISTS idx_user_reports_reported ON user_reports(reported_user_id);
"""

_db: Any = None
_messaging = None
_setup_complete = False


def setup(db, messaging_module=None) -> None:
    """Initialize the reports module."""
    global _db, _messaging, _setup_complete
    
    _db = db
    _messaging = messaging_module
    _setup_complete = True
    _create_tables()
    logger.info("Reports module initialized")


def is_setup() -> bool:
    """Check if module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError("Reports module not initialized. Call reports.setup(db) first.")
    return _db


def _create_tables() -> None:
    """Create report tables."""
    db = _get_db()
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    for statement in statements:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to create reports table: {e}")


def _get_config(key: str, default: Any = None) -> Any:
    """Get reports configuration value."""
    reports_config = config.get("reports", {})
    return reports_config.get(key, default)


# === Message Reports ===

def report_message(
    reporter_id: int,
    message_id: int,
    channel_id: int,
    reason: str,
    category: str = "other",
    details: Optional[str] = None,
    server_id: Optional[int] = None,
    reported_user_id: Optional[int] = None,
    message_content: Optional[str] = None
) -> MessageReport:
    """
    Report a message for content moderation.
    
    Args:
        reporter_id: User ID of reporter
        message_id: ID of the message being reported
        channel_id: Channel containing the message
        reason: Brief reason for report
        category: Report category
        details: Additional details
        server_id: Server ID (if applicable)
        reported_user_id: Author of the message
        message_content: Snapshot of message content
    
    Returns:
        MessageReport object
    """
    db = _get_db()
    
    report_id = generate_snowflake_id()
    now = int(time.time() * 1000)
    
    db.execute(
        """INSERT INTO message_reports 
           (id, message_id, channel_id, server_id, reporter_id, reported_user_id,
            reason, category, details, message_content, status, reported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (report_id, message_id, channel_id, server_id, reporter_id, reported_user_id or 0,
         reason, category, details, message_content, now)
    )
    
    logger.info(f"Message {message_id} reported by user {reporter_id}: {reason}")
    
    return MessageReport(
        id=report_id,
        message_id=message_id,
        channel_id=channel_id,
        server_id=server_id,
        reporter_id=reporter_id,
        reported_user_id=reported_user_id or 0,
        reason=reason,
        category=category,
        details=details,
        message_content=message_content,
        status=ReportStatus.PENDING,
        reported_at=now,
        reviewed_at=None,
        reviewed_by=None,
        admin_notes=None,
        action_taken=None
    )


def get_message_report(report_id: int) -> Optional[MessageReport]:
    """Get a message report by ID."""
    db = _get_db()
    
    row = db.fetch_one(
        "SELECT * FROM message_reports WHERE id = ?",
        (report_id,)
    )
    
    if not row:
        return None
    
    return _row_to_message_report(row)


def get_message_reports(
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[MessageReport]:
    """Get message reports for admin review."""
    db = _get_db()
    
    if status_filter:
        rows = db.fetch_all(
            """SELECT * FROM message_reports 
               WHERE status = ?
               ORDER BY reported_at DESC
               LIMIT ? OFFSET ?""",
            (status_filter, limit, offset)
        )
    else:
        rows = db.fetch_all(
            """SELECT * FROM message_reports 
               ORDER BY reported_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )
    
    return [_row_to_message_report(row) for row in rows]


def get_message_report_counts() -> Dict[str, int]:
    """Get counts of message reports by status."""
    db = _get_db()
    
    counts = {"pending": 0, "reviewed": 0, "actioned": 0, "dismissed": 0, "total": 0}
    
    rows = db.fetch_all(
        "SELECT status, COUNT(*) as count FROM message_reports GROUP BY status"
    )
    
    for row in rows:
        status = row["status"] if isinstance(row, dict) else row[0]
        count = row["count"] if isinstance(row, dict) else row[1]
        if status in counts:
            counts[status] = count
        counts["total"] += count
    
    return counts


def review_message_report(
    report_id: int,
    admin_id: int,
    action: str,
    notes: Optional[str] = None
) -> bool:
    """
    Review a message report.
    
    Args:
        report_id: Report ID
        admin_id: Admin user ID
        action: 'action', 'dismiss', or 'review'
        notes: Admin notes
    
    Returns:
        True if successful
    """
    db = _get_db()
    now = int(time.time() * 1000)
    
    if action == "action":
        status = "actioned"
    elif action == "dismiss":
        status = "dismissed"
    else:
        status = "reviewed"
    
    result = db.execute(
        """UPDATE message_reports 
           SET status = ?, reviewed_at = ?, reviewed_by = ?, 
               admin_notes = ?, action_taken = ?
           WHERE id = ?""",
        (status, now, admin_id, notes, action, report_id)
    )
    
    affected = result.rowcount if hasattr(result, 'rowcount') else 1
    return affected > 0


def _row_to_message_report(row) -> MessageReport:
    """Convert database row to MessageReport."""
    if isinstance(row, dict):
        return MessageReport(
            id=row["id"],
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            server_id=row.get("server_id"),
            reporter_id=row["reporter_id"],
            reported_user_id=row["reported_user_id"],
            reason=row["reason"],
            category=row.get("category", "other"),
            details=row.get("details"),
            message_content=row.get("message_content"),
            status=ReportStatus(row["status"]),
            reported_at=row["reported_at"],
            reviewed_at=row.get("reviewed_at"),
            reviewed_by=row.get("reviewed_by"),
            admin_notes=row.get("admin_notes"),
            action_taken=row.get("action_taken")
        )
    else:
        return MessageReport(
            id=row[0], message_id=row[1], channel_id=row[2],
            server_id=row[3], reporter_id=row[4], reported_user_id=row[5],
            reason=row[6], category=row[7], details=row[8],
            message_content=row[9], status=ReportStatus(row[10]),
            reported_at=row[11], reviewed_at=row[12], reviewed_by=row[13],
            admin_notes=row[14], action_taken=row[15]
        )


# === User Reports ===

def report_user(
    reporter_id: int,
    reported_user_id: int,
    reason: str,
    category: str = "other",
    details: Optional[str] = None,
    evidence_message_ids: Optional[List[int]] = None
) -> UserReport:
    """
    Report a user for behavior issues.
    
    Args:
        reporter_id: User ID of reporter
        reported_user_id: User being reported
        reason: Brief reason for report
        category: Report category
        details: Additional details
        evidence_message_ids: List of message IDs as evidence
    
    Returns:
        UserReport object
    """
    db = _get_db()
    
    report_id = generate_snowflake_id()
    now = int(time.time() * 1000)
    
    evidence_str = None
    if evidence_message_ids:
        evidence_str = ",".join(str(mid) for mid in evidence_message_ids)
    
    db.execute(
        """INSERT INTO user_reports 
           (id, reported_user_id, reporter_id, reason, category, details,
            evidence_message_ids, status, reported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (report_id, reported_user_id, reporter_id, reason, category, 
         details, evidence_str, now)
    )
    
    logger.info(f"User {reported_user_id} reported by user {reporter_id}: {reason}")
    
    return UserReport(
        id=report_id,
        reported_user_id=reported_user_id,
        reporter_id=reporter_id,
        reason=reason,
        category=category,
        details=details,
        evidence_message_ids=evidence_message_ids,
        status=ReportStatus.PENDING,
        reported_at=now,
        reviewed_at=None,
        reviewed_by=None,
        admin_notes=None,
        action_taken=None
    )


def get_user_report(report_id: int) -> Optional[UserReport]:
    """Get a user report by ID."""
    db = _get_db()
    
    row = db.fetch_one(
        "SELECT * FROM user_reports WHERE id = ?",
        (report_id,)
    )
    
    if not row:
        return None
    
    return _row_to_user_report(row)


def get_user_reports(
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[UserReport]:
    """Get user reports for admin review."""
    db = _get_db()
    
    if status_filter:
        rows = db.fetch_all(
            """SELECT * FROM user_reports 
               WHERE status = ?
               ORDER BY reported_at DESC
               LIMIT ? OFFSET ?""",
            (status_filter, limit, offset)
        )
    else:
        rows = db.fetch_all(
            """SELECT * FROM user_reports 
               ORDER BY reported_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        )
    
    return [_row_to_user_report(row) for row in rows]


def get_user_report_counts() -> Dict[str, int]:
    """Get counts of user reports by status."""
    db = _get_db()
    
    counts = {"pending": 0, "reviewed": 0, "actioned": 0, "dismissed": 0, "total": 0}
    
    rows = db.fetch_all(
        "SELECT status, COUNT(*) as count FROM user_reports GROUP BY status"
    )
    
    for row in rows:
        status = row["status"] if isinstance(row, dict) else row[0]
        count = row["count"] if isinstance(row, dict) else row[1]
        if status in counts:
            counts[status] = count
        counts["total"] += count
    
    return counts


def review_user_report(
    report_id: int,
    admin_id: int,
    action: str,
    notes: Optional[str] = None
) -> bool:
    """
    Review a user report.
    
    Args:
        report_id: Report ID
        admin_id: Admin user ID
        action: 'action', 'dismiss', or 'review'
        notes: Admin notes
    
    Returns:
        True if successful
    """
    db = _get_db()
    now = int(time.time() * 1000)
    
    if action == "action":
        status = "actioned"
    elif action == "dismiss":
        status = "dismissed"
    else:
        status = "reviewed"
    
    result = db.execute(
        """UPDATE user_reports 
           SET status = ?, reviewed_at = ?, reviewed_by = ?, 
               admin_notes = ?, action_taken = ?
           WHERE id = ?""",
        (status, now, admin_id, notes, action, report_id)
    )
    
    affected = result.rowcount if hasattr(result, 'rowcount') else 1
    return affected > 0


def _row_to_user_report(row) -> UserReport:
    """Convert database row to UserReport."""
    evidence_ids = None
    
    if isinstance(row, dict):
        evidence_str = row.get("evidence_message_ids")
        if evidence_str:
            evidence_ids = [int(x) for x in evidence_str.split(",") if x]
        
        return UserReport(
            id=row["id"],
            reported_user_id=row["reported_user_id"],
            reporter_id=row["reporter_id"],
            reason=row["reason"],
            category=row.get("category", "other"),
            details=row.get("details"),
            evidence_message_ids=evidence_ids,
            status=ReportStatus(row["status"]),
            reported_at=row["reported_at"],
            reviewed_at=row.get("reviewed_at"),
            reviewed_by=row.get("reviewed_by"),
            admin_notes=row.get("admin_notes"),
            action_taken=row.get("action_taken")
        )
    else:
        evidence_str = row[6]
        if evidence_str:
            evidence_ids = [int(x) for x in evidence_str.split(",") if x]
        
        return UserReport(
            id=row[0], reported_user_id=row[1], reporter_id=row[2],
            reason=row[3], category=row[4], details=row[5],
            evidence_message_ids=evidence_ids, status=ReportStatus(row[7]),
            reported_at=row[8], reviewed_at=row[9], reviewed_by=row[10],
            admin_notes=row[11], action_taken=row[12]
        )
