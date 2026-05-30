"""
Reporting operations mixin for DeduplicationManager.
"""

import os
import time
from typing import Any, Dict, List, Optional

import utils.logger as logger

from .constants import HashReport, ReportStatus


class ReportingMixin:
    """Provides reporting operations for content moderation."""

    __slots__ = ()

    _db: Any
    _config: dict[str, Any]

    def block_hash(
        self,
        hash_value: str,
        reason: str,
        blocked_by: Optional[int] = None,
        auto: bool = False,
        hash_type: str = "sha256",
        phash_threshold: int = 10,
    ) -> bool: ...

    def _get_report_count(self, hash_value: str) -> int:
        """Get number of reports for a hash."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM media_hash_reports WHERE hash_value = ? AND status = 'pending'",
            (hash_value,),
        )
        return row["count"] if isinstance(row, dict) else row[0] if row else 0

    def report_hash(
        self,
        hash_value: str,
        reporter_id: int,
        reason: str,
        details: Optional[str] = None,
        phash_value: Optional[str] = None,
        uploader_id: Optional[int] = None,
        message_id: Optional[int] = None,
        attachment_url: Optional[str] = None,
        block_uploader: bool = False,
    ) -> int:
        """Report a file hash for content moderation."""
        from src.utils.encryption import generate_snowflake_id

        now = int(time.time() * 1000)

        if hash_value == "URL_REPORT" and attachment_url:
            filename = os.path.basename(attachment_url.split("?")[0])
            row = self._db.fetch_one(
                "SELECT hash_value, phash_value FROM media_file_hashes WHERE storage_path LIKE ?",
                (f"%{filename}",),
            )
            if row:
                hash_value = row["hash_value"] or hash_value
                if not phash_value:
                    phash_value = row.get("phash_value")

        report_id = generate_snowflake_id()

        self._db.execute(
            """INSERT INTO media_hash_reports
               (id, hash_value, phash_value, reporter_id, reason, details, status,
                reported_at, uploader_id, message_id, attachment_url, block_uploader)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (
                report_id,
                hash_value,
                phash_value,
                reporter_id,
                reason,
                details,
                now,
                uploader_id,
                message_id,
                attachment_url,
                1 if block_uploader else 0,
            ),
        )

        report_count = self._get_report_count(hash_value)
        if report_count >= self._config["auto_block_threshold"]:
            self.block_hash(
                hash_value, f"Auto-blocked: {report_count} reports", auto=True
            )

        logger.info(
            f"Hash {hash_value[:16]}... reported by user {reporter_id}: {reason}"
        )
        return report_id

    def get_reports(
        self, status_filter: Optional[str] = None, limit: int = 50, offset: int = 0
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
                (status_filter, limit, offset),
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
                (limit, offset),
            )

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
                        status=ReportStatus(row["status"]),
                        reported_at=row["reported_at"],
                        reviewed_at=row["reviewed_at"],
                        reviewed_by=row["reviewed_by"],
                        admin_notes=row["admin_notes"],
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
                        status=ReportStatus(row[6]),
                        reported_at=row[7],
                        reviewed_at=row[8],
                        reviewed_by=row[9],
                        admin_notes=row[10],
                    )
                )

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

    def review_report(
        self, report_id: int, admin_id: int, action: str, notes: Optional[str] = None
    ) -> bool:
        """Review a hash report."""
        now = int(time.time() * 1000)

        row = self._db.fetch_one(
            "SELECT hash_value FROM media_hash_reports WHERE id = ?", (report_id,)
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
            (status, now, admin_id, notes, report_id),
        )

        return True
