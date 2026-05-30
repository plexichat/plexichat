"""
Password management mixin providing check_password_change_required, change_password, and change_admin_password.
"""

import time
from typing import Tuple

import utils.config as config

from typing import Any


from .helpers import _sync_password_to_auth_users, _verify_admin_password


class PasswordMixin:
    _db: Any

    """Password policy and change management."""

    def check_password_change_required(self, admin_id: int) -> bool:
        """Check if admin is required to change password."""
        admin_config = config.get("admin_ui", {})

        if not admin_config.get("force_password_change_first_login", True):
            return False

        row = self._db.fetch_one(
            "SELECT force_password_change, last_password_change FROM admin_users WHERE id = ?",
            (admin_id,),
        )

        if not row:
            return False

        if isinstance(row, dict):
            force_change = bool(row.get("force_password_change", 0))
            last_password_change = row.get("last_password_change")
        else:
            force_change = bool(row[0]) if len(row) > 0 else False
            last_password_change = row[1] if len(row) > 1 else None

        if force_change:
            return True

        password_policy = admin_config.get("security", {}).get("password_policy", {})
        change_interval_days = password_policy.get("change_interval_days", 90)

        if last_password_change and change_interval_days > 0:
            now = int(time.time())
            days_since_change = (now - last_password_change) / (24 * 3600 * 1000)
            if days_since_change >= change_interval_days:
                return True

        return False

    def _validate_password_policy(self, new_password: str) -> Tuple[bool, str]:
        """Validate new password against configured policy. Returns (valid, message)."""
        admin_config = config.get("admin_ui", {})
        password_policy = admin_config.get("security", {}).get("password_policy", {})

        min_length = password_policy.get("min_length", 12)
        require_uppercase = password_policy.get("require_uppercase", True)
        require_lowercase = password_policy.get("require_lowercase", True)
        require_numbers = password_policy.get("require_numbers", True)
        require_special_chars = password_policy.get("require_special_chars", True)
        prevent_common_passwords = password_policy.get("prevent_common_passwords", True)

        if len(new_password) < min_length:
            return False, f"Password must be at least {min_length} characters"

        if require_uppercase and not any(c.isupper() for c in new_password):
            return False, "Password must contain at least one uppercase letter"

        if require_lowercase and not any(c.islower() for c in new_password):
            return False, "Password must contain at least one lowercase letter"

        if require_numbers and not any(c.isdigit() for c in new_password):
            return False, "Password must contain at least one number"

        if require_special_chars and not any(
            c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in new_password
        ):
            return False, "Password must contain at least one special character"

        if prevent_common_passwords:
            common_passwords = [
                "password",
                "123456",
                "qwerty",
                "admin",
                "letmein",
                "welcome",
                "monkey",
                "dragon",
                "master",
                "hello",
                "football",
                "iloveyou",
            ]
            if new_password.lower() in common_passwords:
                return (
                    False,
                    "Password is too common, please choose a stronger password",
                )

        return True, ""

    def change_password(
        self, admin_id: int, current_password: str, new_password: str
    ) -> Tuple[bool, str]:
        """Change admin password with policy validation."""
        valid, _row, message = _verify_admin_password(
            self._db, admin_id, current_password
        )
        if not valid:
            return False, message

        valid, msg = self._validate_password_policy(new_password)
        if not valid:
            return False, msg

        import src.utils.encryption as encryption

        new_hash = encryption.hash_password(new_password)
        now = int(time.time())

        self._db.execute(
            "UPDATE admin_users SET password_hash = ?, force_password_change = 0, last_password_change = ? WHERE id = ?",
            (new_hash, now, admin_id),
        )

        username = _row["username"] if _row and isinstance(_row, dict) else "admin"
        if username:
            _sync_password_to_auth_users(self._db, str(username), new_hash)
        return True, "Password updated successfully"

    def change_admin_password(
        self, admin_id: int, old_password: str, new_password: str
    ) -> Tuple[bool, str]:
        """Change admin password with validation."""
        import src.utils.encryption as encryption

        row = self._db.fetch_one(
            "SELECT password_hash FROM admin_users WHERE id = ?", (admin_id,)
        )

        if not row:
            return False, "Admin user not found"

        if isinstance(row, dict):
            current_hash = row.get("password_hash")
        else:
            current_hash = row[0]

        if not current_hash or not encryption.verify_password(
            old_password, str(current_hash)
        ):
            return False, "Current password is incorrect"

        valid, msg = self._validate_password_policy(new_password)
        if not valid:
            return False, msg

        new_hash = encryption.hash_password(new_password)
        now = int(time.time())
        self._db.execute(
            "UPDATE admin_users SET password_hash = ?, force_password_change = 0, last_password_change = ? WHERE id = ?",
            (new_hash, now, admin_id),
        )

        admin_row = self._db.fetch_one(
            "SELECT username FROM admin_users WHERE id = ?", (admin_id,)
        )
        if admin_row:
            username = (
                admin_row.get("username")
                if isinstance(admin_row, dict)
                else admin_row[0]
            )
            if username:
                _sync_password_to_auth_users(self._db, str(username), new_hash)

        return True, "Password changed successfully"
