"""
Moderation and content review for PlexiChat Admin.
"""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import time
import utils.logger as logger

@dataclass
class HashReport:
    """A content hash report for moderation."""
    id: int
    hash_value: str
    reporter_id: int
    reporter_username: Optional[str]
    reason: str
    details: Optional[str]
    status: str  # 'pending', 'reviewed', 'blocked', 'cleared'
    reported_at: int
    reviewed_at: Optional[int]
    reviewed_by: Optional[int]
    admin_notes: Optional[str]
    phash_value: Optional[str] = None
    uploader_id: Optional[int] = None
    message_id: Optional[int] = None
    attachment_url: Optional[str] = None
    block_uploader: bool = False

@dataclass
class BlockedHash:
    """A blocked content hash."""
    hash_value: str
    reason: str
    blocked_at: int
    blocked_by: Optional[int]
    auto_blocked: bool
    hash_type: str = "sha256"
    phash_threshold: int = 10

@dataclass
class BlockedUser:
    """A user blocked from uploading media."""
    user_id: int
    username: Optional[str]
    reason: str
    blocked_at: int
    blocked_by: Optional[int]
    expires_at: Optional[int]

def get_hash_reports(db: Any, status_filter: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[HashReport]:
    """Get hash reports for admin review."""
    query = """SELECT r.id, r.hash_value, r.reporter_id, u.username, r.reason, 
                      r.details, r.status, r.reported_at, r.reviewed_at, 
                      r.reviewed_by, r.admin_notes, r.phash_value, r.uploader_id,
                      r.message_id, r.attachment_url, r.block_uploader
               FROM media_hash_reports r
               LEFT JOIN auth_users u ON r.reporter_id = u.id"""

    if status_filter:
        query += " WHERE r.status = ?"
        query += " ORDER BY r.reported_at DESC LIMIT ? OFFSET ?"
        rows = db.fetch_all(query, (status_filter, limit, offset))
    else:
        query += " ORDER BY r.reported_at DESC LIMIT ? OFFSET ?"
        rows = db.fetch_all(query, (limit, offset))

    reports = []
    for row in rows:
        if isinstance(row, dict):
            reports.append(
                HashReport(
                    id=row["id"],
                    hash_value=row["hash_value"],
                    reporter_id=row["reporter_id"],
                    reporter_username=row["username"],
                    reason=row["reason"],
                    details=row["details"],
                    status=row["status"],
                    reported_at=row["reported_at"],
                    reviewed_at=row["reviewed_at"],
                    reviewed_by=row["reviewed_by"],
                    admin_notes=row["admin_notes"],
                    phash_value=row.get("phash_value"),
                    uploader_id=row.get("uploader_id"),
                    message_id=row.get("message_id"),
                    attachment_url=row.get("attachment_url"),
                    block_uploader=bool(row.get("block_uploader", 0)),
                )
            )
        else:
            reports.append(
                HashReport(
                    id=row[0],
                    hash_value=row[1],
                    reporter_id=row[2],
                    reporter_username=row[3],
                    reason=row[4],
                    details=row[5],
                    status=row[6],
                    reported_at=row[7],
                    reviewed_at=row[8],
                    reviewed_by=row[9],
                    admin_notes=row[10],
                    phash_value=row[11] if len(row) > 11 else None,
                    uploader_id=row[12] if len(row) > 12 else None,
                    message_id=row[13] if len(row) > 13 else None,
                    attachment_url=row[14] if len(row) > 14 else None,
                    block_uploader=bool(row[15]) if len(row) > 15 else False,
                )
            )
    return reports

def get_hash_report_counts(db: Any) -> Dict[str, int]:
    """Get counts of hash reports by status."""
    counts = {"pending": 0, "blocked": 0, "cleared": 0, "total": 0}
    try:
        rows = db.fetch_all(
            "SELECT status, COUNT(*) as count FROM media_hash_reports GROUP BY status"
        )
        for row in rows:
            status = row["status"] if isinstance(row, dict) else row[0]
            count = row["count"] if isinstance(row, dict) else row[1]
            if status in counts:
                counts[status] = count
            counts["total"] += count
    except Exception:
        pass
    return counts

def get_blocked_hashes(db: Any, limit: int = 100, offset: int = 0) -> List[BlockedHash]:
    """Get list of blocked hashes."""
    try:
        rows = db.fetch_all(
            """SELECT hash_value, reason, blocked_at, blocked_by, auto_blocked,
                      hash_type, phash_threshold
               FROM media_blocked_hashes
               ORDER BY blocked_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(
                    BlockedHash(
                        hash_value=row["hash_value"],
                        reason=row["reason"],
                        blocked_at=row["blocked_at"],
                        blocked_by=row["blocked_by"],
                        auto_blocked=bool(row["auto_blocked"]),
                        hash_type=row.get("hash_type", "sha256"),
                        phash_threshold=row.get("phash_threshold", 10),
                    )
                )
            else:
                result.append(
                    BlockedHash(
                        hash_value=row[0],
                        reason=row[1],
                        blocked_at=row[2],
                        blocked_by=row[3],
                        auto_blocked=bool(row[4]),
                        hash_type=row[5] if len(row) > 5 else "sha256",
                        phash_threshold=row[6] if len(row) > 6 else 10,
                    )
                )
        return result
    except Exception:
        return []

def get_blocked_hash_count(db: Any) -> int:
    """Get count of blocked hashes."""
    try:
        row = db.fetch_one("SELECT COUNT(*) as count FROM media_blocked_hashes")
        return row["count"] if isinstance(row, dict) else row[0] if row else 0
    except Exception:
        return 0

def review_hash_report(db: Any, report_id: int, admin_id: int, action: str, notes: Optional[str] = None) -> bool:
    """Review a hash report."""
    now = int(time.time() * 1000)
    row = db.fetch_one(
        "SELECT hash_value FROM media_hash_reports WHERE id = ?", (report_id,)
    )
    if not row:
        return False

    hash_value = row["hash_value"] if isinstance(row, dict) else row[0]

    if action == "block":
        try:
            db.upsert(
                "media_blocked_hashes",
                ["hash_value", "reason", "blocked_at", "blocked_by", "auto_blocked"],
                (hash_value, notes or "Blocked by admin", now, admin_id, 0),
                conflict_columns=["hash_value"]
            )
        except Exception as e:
            logger.error(f"Failed to block hash: {e}")
            return False
        status = "blocked"
    elif action == "clear":
        status = "cleared"
    else:
        status = "reviewed"

    db.execute(
        """UPDATE media_hash_reports 
           SET status = ?, reviewed_at = ?, reviewed_by = ?, admin_notes = ?
           WHERE id = ?""",
        (status, now, admin_id, notes, report_id),
    )
    return True

def unblock_hash(db: Any, hash_value: str) -> bool:
    """Unblock a hash."""
    try:
        db.execute(
            "DELETE FROM media_blocked_hashes WHERE hash_value = ?", (hash_value,)
        )
        return True
    except Exception as e:
        logger.error(f"Failed to unblock hash: {e}")
        return False

def block_hash(db: Any, hash_value: str, reason: str, admin_id: int, hash_type: str = "sha256", phash_threshold: int = 10) -> bool:
    """Manually block a hash."""
    now = int(time.time() * 1000)
    try:
        db.upsert(
            "media_blocked_hashes",
            ["hash_value", "hash_type", "phash_threshold", "reason", "blocked_at", "blocked_by", "auto_blocked"],
            (hash_value, hash_type, phash_threshold, reason, now, admin_id, 0),
            conflict_columns=["hash_value"]
        )
        return True
    except Exception as e:
        logger.error(f"Failed to block hash: {e}")
        return False

def get_blocked_users(db: Any, limit: int = 100, offset: int = 0) -> List[BlockedUser]:
    """Get list of users blocked from uploading media."""
    try:
        rows = db.fetch_all(
            """SELECT b.user_id, u.username, b.reason, b.blocked_at, b.blocked_by, b.expires_at
               FROM media_blocked_users b
               LEFT JOIN auth_users u ON b.user_id = u.id
               ORDER BY b.blocked_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(
                    BlockedUser(
                        user_id=row["user_id"],
                        username=row.get("username"),
                        reason=row["reason"],
                        blocked_at=row["blocked_at"],
                        blocked_by=row["blocked_by"],
                        expires_at=row.get("expires_at"),
                    )
                )
            else:
                result.append(
                    BlockedUser(
                        user_id=row[0],
                        username=row[1],
                        reason=row[2],
                        blocked_at=row[3],
                        blocked_by=row[4],
                        expires_at=row[5] if len(row) > 5 else None,
                    )
                )
        return result
    except Exception:
        return []

def block_user(db: Any, user_id: int, reason: str, admin_id: int, duration_hours: Optional[int] = None) -> bool:
    """Block a user from uploading media."""
    now = int(time.time() * 1000)
    expires_at = None
    if duration_hours:
        expires_at = now + (duration_hours * 3600 * 1000)

    try:
        db.upsert(
            "media_blocked_users",
            ["user_id", "reason", "blocked_at", "blocked_by", "expires_at"],
            (user_id, reason, now, admin_id, expires_at),
            conflict_columns=["user_id"]
        )
        logger.info(f"Admin {admin_id} blocked user {user_id} from uploads: {reason}")
        return True
    except Exception as e:
        logger.error(f"Failed to block user: {e}")
        return False

def unblock_user(db: Any, user_id: int) -> bool:
    """Unblock a user from uploading media."""
    try:
        db.execute("DELETE FROM media_blocked_users WHERE user_id = ?", (user_id,))
        return True
    except Exception as e:
        logger.error(f"Failed to unblock user: {e}")
        return False
