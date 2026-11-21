"""
Admin logging system with dual file/database logging support.

This module provides enhanced logging for admin actions that writes to both
file logs and the admin_audit_log database table for comprehensive audit trails.
"""

import logging
import time
import json
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    pass


# Every admin audit write used to ask the database twice whether
# ``admin_audit_log.target_user_id`` exists (``column_exists`` performs a
# ``sqlite_master`` lookup plus ``PRAGMA table_info``, or an
# ``information_schema`` query on PostgreSQL) just to pick between two INSERT
# shapes. That schema fact is static for the life of a process, so cache the
# verdict on the (singleton) logger.
#
# Safety properties:
# * bounded: a hard cap on cached verdicts, keyed only by static
#   ``(table, column)`` literals -- no request or user data ever enters a key;
# * fail-closed: an introspection error is never cached and still takes the
#   historical "extended schema" branch, so the INSERT surfaces the real error
#   instead of silently writing a truncated row;
# * multi-node safe: a short TTL bounds staleness so a migration that adds the
#   column on any node is picked up without a process restart, and an explicit
#   invalidation hook allows an immediate refresh.
_SCHEMA_VERDICT_TTL_SECONDS = 30.0
_SCHEMA_VERDICT_MAX_ENTRIES = 16


@dataclass
class AdminLogEntry:
    """Structured admin log entry."""

    admin_id: int
    action: str
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    status: str = "success"
    metadata: Optional[Dict[str, Any]] = None


class AdminLogger:
    """
    Dual-logging system for admin actions.

    Writes admin actions to both file logs and the admin_audit_log database table
    for comprehensive audit trails and compliance.
    """

    def __init__(self, file_logger: Optional[logging.Logger] = None):
        """
        Initialize the admin logger.

        Args:
            file_logger: Optional file logger for traditional log file output
        """
        self.file_logger = file_logger or logging.getLogger("admin")
        self._db_logging_enabled = True
        # {(table, column): (expires_at_monotonic, verdict)}
        self._schema_verdicts: Dict[Tuple[str, str], Tuple[float, bool]] = {}

    def set_db_logging(self, enabled: bool):
        """Enable or disable database logging."""
        self._db_logging_enabled = enabled

    def clear_schema_cache(self) -> None:
        """Drop cached schema verdicts (e.g. after a migration adds a column)."""
        self._schema_verdicts.clear()

    def _column_exists_cached(self, db: Any, table: str, column: str) -> bool:
        """Return ``db.column_exists(...)`` with a bounded, short-TTL memo.

        A failed introspection is deliberately not cached (fail-closed): the
        caller keeps the historical "assume extended schema" behaviour and the
        next write retries the check instead of pinning a bad verdict.
        """
        key = (table, column)
        now = time.monotonic()
        cached = self._schema_verdicts.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            verdict = bool(db.column_exists(table, column))
        except Exception:
            return True
        if len(self._schema_verdicts) >= _SCHEMA_VERDICT_MAX_ENTRIES:
            self._schema_verdicts.clear()
        self._schema_verdicts[key] = (now + _SCHEMA_VERDICT_TTL_SECONDS, verdict)
        return verdict

    def log_action(
        self,
        db: Any,
        entry: AdminLogEntry,
        log_to_file: bool = True,
        log_to_db: bool = True,
    ) -> bool:
        """
        Log an admin action to both file and database.

        Args:
            db: Database connection
            entry: AdminLogEntry with action details
            log_to_file: Whether to write to file logs
            log_to_db: Whether to write to database

        Returns:
            True if logging succeeded, False otherwise
        """
        success = True

        # Log to file
        if log_to_file and self.file_logger:
            try:
                log_message = self._format_log_message(entry)
                log_level = (
                    logging.INFO if entry.status == "success" else logging.WARNING
                )
                self.file_logger.log(log_level, log_message)
            except Exception:
                success = False
                self.file_logger.exception("Failed to log admin action to file")

        # Log to database
        if log_to_db and self._db_logging_enabled:
            try:
                self._log_to_database(db, entry)
            except Exception:
                success = False
                self.file_logger.exception("Failed to log admin action to database")

        return success

    def _format_log_message(self, entry: AdminLogEntry) -> str:
        """Format admin log entry for file logging."""
        parts = [
            f"AdminID={entry.admin_id}",
            f"Action={entry.action}",
        ]

        if entry.target_type:
            parts.append(f"Target={entry.target_type}")
            if entry.target_id:
                parts.append(f"TargetID={entry.target_id}")

        if entry.ip_address:
            parts.append(f"IP={entry.ip_address}")

        if entry.status != "success":
            parts.append(f"Status={entry.status}")

        if entry.details:
            parts.append(f"Details={entry.details}")

        if entry.metadata:
            parts.append(f"Metadata={json.dumps(entry.metadata)}")

        return " ".join(parts)

    def _log_to_database(self, db: Any, entry: AdminLogEntry):
        """Write admin log entry to database."""
        now = int(time.time() * 1000)

        # Serialize metadata if present
        details_json = entry.details
        target_user_id = None

        if entry.metadata:
            metadata_str = json.dumps(entry.metadata)
            if details_json:
                details_json = f"{details_json} | {metadata_str}"
            else:
                details_json = metadata_str

            # Extract target_user_id from metadata if present
            if "target_user_id" in entry.metadata:
                target_user_id = entry.metadata["target_user_id"]

        # If target_type is 'user', target_id is the user_id
        if not target_user_id and entry.target_type == "user":
            target_user_id = entry.target_id

        has_target_user_id = self._column_exists_cached(
            db, "admin_audit_log", "target_user_id"
        )

        if has_target_user_id:
            db.execute(
                """
                INSERT INTO admin_audit_log
                (admin_id, action, target_type, target_id, target_user_id, details, ip_address, user_agent, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entry.admin_id,
                    entry.action,
                    entry.target_type,
                    entry.target_id,
                    target_user_id,
                    details_json,
                    entry.ip_address,
                    entry.user_agent,
                    entry.status,
                    now,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO admin_audit_log
                (admin_id, action, target_type, target_id, details, ip_address, user_agent, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entry.admin_id,
                    entry.action,
                    entry.target_type,
                    entry.target_id,
                    details_json,
                    entry.ip_address,
                    entry.user_agent,
                    entry.status,
                    now,
                ),
            )

        # A successful write changes "today's" audit totals. Drop the short-TTL
        # summary so the next dashboard poll recomputes immediately instead of
        # serving stale counts for the rest of the window. Best-effort: a missed
        # invalidation only delays freshness by the cache TTL (and never affects
        # whether the audit row itself was written). Imported lazily to avoid a
        # core -> api import cycle at module load time.
        try:
            from src.api.routes.admin._summary_cache import clear as _clear_summary

            _clear_summary("audit_stats")
        except Exception:
            pass

    def log_login(
        self,
        db: Any,
        admin_id: int,
        success: bool,
        ip_address: str,
        user_agent: Optional[str] = None,
    ):
        """Log admin login attempt."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action="login",
            status="success" if success else "failed",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return self.log_action(db, entry)

    def log_logout(self, db: Any, admin_id: int, ip_address: str):
        """Log admin logout."""
        entry = AdminLogEntry(admin_id=admin_id, action="logout", ip_address=ip_address)
        return self.log_action(db, entry)

    def log_password_change(
        self, db: Any, admin_id: int, success: bool, ip_address: str
    ):
        """Log admin password change."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action="password_change",
            status="success" if success else "failed",
            ip_address=ip_address,
        )
        return self.log_action(db, entry)

    def log_otp_setup(self, db: Any, admin_id: int, success: bool, ip_address: str):
        """Log admin OTP setup."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action="otp_setup",
            status="success" if success else "failed",
            ip_address=ip_address,
        )
        return self.log_action(db, entry)

    def log_user_action(
        self,
        db: Any,
        admin_id: int,
        action: str,
        target_user_id: int,
        details: Optional[str] = None,
        ip_address: Optional[str] = None,
    ):
        """Log admin action on a user."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action=action,
            target_type="user",
            target_id=target_user_id,
            details=details,
            ip_address=ip_address,
        )
        return self.log_action(db, entry)

    def log_server_action(
        self,
        db: Any,
        admin_id: int,
        action: str,
        target_server_id: int,
        details: Optional[str] = None,
        ip_address: Optional[str] = None,
    ):
        """Log admin action on a server."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action=action,
            target_type="server",
            target_id=target_server_id,
            details=details,
            ip_address=ip_address,
        )
        return self.log_action(db, entry)

    def log_role_change(
        self,
        db: Any,
        admin_id: int,
        target_admin_id: int,
        role_id: int,
        action: str,  # "assign" or "revoke"
        ip_address: Optional[str] = None,
    ):
        """Log admin role assignment/revocation."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action=f"role_{action}",
            target_type="admin",
            target_id=target_admin_id,
            details=f"RoleID={role_id}",
            ip_address=ip_address,
        )
        return self.log_action(db, entry)

    def log_approval_request(
        self,
        db: Any,
        admin_id: int,
        action_type: str,
        target_type: Optional[str],
        target_id: Optional[int],
        details: Optional[str] = None,
        ip_address: Optional[str] = None,
    ):
        """Log approval workflow request."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action=f"approval_request:{action_type}",
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip_address=ip_address,
        )
        return self.log_action(db, entry)

    def log_approval_decision(
        self,
        db: Any,
        admin_id: int,
        approval_id: int,
        decision: str,  # "approve" or "reject"
        ip_address: Optional[str] = None,
    ):
        """Log approval workflow decision."""
        entry = AdminLogEntry(
            admin_id=admin_id,
            action=f"approval_{decision}",
            target_type="approval",
            target_id=approval_id,
            ip_address=ip_address,
        )
        return self.log_action(db, entry)


# Global admin logger instance
_admin_logger: Optional[AdminLogger] = None


def get_admin_logger() -> AdminLogger:
    """Get the global admin logger instance."""
    global _admin_logger
    if _admin_logger is None:
        _admin_logger = AdminLogger()
    return _admin_logger


def init_admin_logger(file_logger: Optional[logging.Logger] = None) -> AdminLogger:
    """Initialize the global admin logger."""
    global _admin_logger
    _admin_logger = AdminLogger(file_logger)
    return _admin_logger


def clear_admin_schema_cache() -> None:
    """Invalidate the singleton logger's cached schema verdicts.

    Call this after a migration changes ``admin_audit_log`` so the next write
    re-introspects the schema immediately instead of waiting for the TTL.
    """
    if _admin_logger is not None:
        _admin_logger.clear_schema_cache()
