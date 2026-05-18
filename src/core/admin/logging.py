"""
Admin logging system with dual file/database logging support.

This module provides enhanced logging for admin actions that writes to both
file logs and the admin_audit_log database table for comprehensive audit trails.
"""

import logging
import time
import json
from typing import Optional, Dict, Any, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    pass


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

    def set_db_logging(self, enabled: bool):
        """Enable or disable database logging."""
        self._db_logging_enabled = enabled

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
            except Exception as e:
                success = False
                print(f"Failed to log admin action to file: {e}")

        # Log to database
        if log_to_db and self._db_logging_enabled:
            try:
                self._log_to_database(db, entry)
            except Exception as e:
                success = False
                print(f"Failed to log admin action to database: {e}")

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
        if entry.metadata:
            metadata_str = json.dumps(entry.metadata)
            if details_json:
                details_json = f"{details_json} | {metadata_str}"
            else:
                details_json = metadata_str

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
