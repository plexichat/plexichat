"""
Identity collector for auth_users table.

Collects core user identity data including authentication fields.
Redacts sensitive fields like passwords, TOTP secrets, and encrypted emails.
"""

from typing import Any, Dict

from ..base import BaseCollector


class IdentityCollector(BaseCollector):
    """Collects identity data from auth_users table."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect identity data."""
        try:
            user = self._db.fetch_one(
                """
                SELECT id, account_type, username, email_index, email_encrypted,
                       created_at, updated_at, email_verified, account_locked,
                       locked_until, failed_login_attempts, last_login_at,
                       totp_enabled, avatar_url, age_verified, date_of_birth,
                       deletion_status, deletion_at, custom_status_text,
                       custom_status_emoji, custom_status_expires_at
                FROM auth_users WHERE id = ?
                """,
                (user_id,),
            )
            if not user:
                return {}

            result = dict(user)
            # Redact sensitive fields
            for field in (
                "password_hash",
                "totp_secret_encrypted",
                "backup_codes_hash",
            ):
                if field in result:
                    del result[field]
            if result.get("email_encrypted"):
                result["email_encrypted"] = "(encrypted)"

            return result
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect identity for user {user_id}: {e}")
            return {}
