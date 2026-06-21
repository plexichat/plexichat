"""
Password management mixin providing check_password_change_required, change_password, and change_admin_password.
"""

import time
from typing import Tuple, Optional

import utils.config as config
import utils.logger as logger

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

    def admin_force_password_change_target(
        self,
        acting_admin_id: int,
        target_admin_id: int,
        client_ip: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Deprecated mixin entry point — use the free function
        :func:`force_password_change_target` in this module instead.
        Retained so existing imports keep working.
        """
        return force_password_change_target(
            db=self._db,
            acting_admin_id=acting_admin_id,
            target_admin_id=target_admin_id,
            client_ip=client_ip,
        )


def force_password_change_target(
    db: Any,
    acting_admin_id: int,
    target_admin_id: int,
    client_ip: Optional[str] = None,
) -> Tuple[bool, str]:
    """Force another administrator to rotate their password on next login.

    This is a **service-layer** helper.  The route layer MUST already
    have validated the caller has ``admin.edit`` permission via
    ``require_admin_permission``; this function additionally:

    * coerces ``target_admin_id`` to ``int`` and rejects invalid input,
    * confirms the target admin exists,
    * refuses to target oneself (use :py:meth:`PasswordMixin.change_password`
      for self-service rotation),
    * refuses the privileged mutation (`return False`) when the audit
      module can't be imported — a force-password-change without a
      guaranteed audit row would break the audit-trail invariant,
    * emits the audit-log row under the same atomic boundary as the SQL
      UPDATE so a "mutated-but-un-audited" row cannot exist.

    Audit-trail invariant has two paths depending on driver transaction
    support:

    * **Transactional drivers** (Postgres, recent SQLite): BEGIN first,
      then UPDATE + audit_log + COMMIT under one boundary.  Failed audit
      rolls the UPDATE back via ``db.rollback()``.
    * **Non-transactional drivers** (SQLite autocommit, ephemeral test
      backends, OR a transactional driver whose ``BEGIN`` just raised):
      audit FIRST, then UPDATE only if audit succeeded.  This blocks the
      "mutated-but-un-audited" tail-state race that earlier
      implementations accepted as a CRITICAL-only failure.

    Implementation lives outside :class:`PasswordMixin` so it can be
    called as a plain function with an explicit ``db`` argument, without
    the caller having to spin up a mixin pseudo-instance just to bind
    ``_db``.
    """
    try:
        target_id = int(target_admin_id)
    except (TypeError, ValueError):
        return False, "Invalid admin ID"

    if int(acting_admin_id) == target_id:
        return False, "Use change_password() to rotate your own credentials"

    row = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?",
        (target_id,),
    )
    if not row:
        return False, "Target admin does not exist"

    # Resolve the audit-log entry point up-front.  If the module is
    # unavailable we refuse the privileged mutation rather than
    # silently mutate state.
    try:
        from src.core.admin.permissions import log_admin_action
    except Exception:  # pragma: no cover -- audit module unavailable
        log_admin_action = None  # type: ignore[assignment]

    if log_admin_action is None:
        logger.critical(
            "AUDIT-MISMATCH: force_password_change could not import "
            "audit module for admin %s -> %s; refusing mutation",
            acting_admin_id,
            target_id,
        )
        return False, "Audit module unavailable"

    # Attempt to open a transaction.  If BEGIN raises mid-flight, we
    # flip ``rollback_supported`` to False and the audit-first gate
    # below re-runs.  Doing BEGIN FIRST (rather than letting the
    # audit-first branch skip entirely when we entered with
    # rollback_supported=True) closes the prior mid-flight invariant
    # break the reviewer flagged.
    rollback_supported = hasattr(db, "begin_transaction") and hasattr(db, "commit")
    if rollback_supported:
        try:
            db.begin_transaction()
        except Exception as tx_exc:  # pragma: no cover
            rollback_supported = False
            logger.warning(
                "force_password_change: transaction begin failed (%s); "
                "falling back to audit-first non-atomic write",
                tx_exc,
            )

    # Audit-first path: when no transaction can protect us, the audit
    # row must be persisted BEFORE the UPDATE so a failing audit
    # never leaves the DB in a mutated-but-un-audited state.
    if not rollback_supported:
        try:
            log_admin_action(
                db,
                acting_admin_id,
                "force_password_change",
                "admin_user",
                target_id,
                {"message": f"Forced password change for admin {target_id}"},
                client_ip or "unknown",
            )
        except Exception as audit_exc:
            logger.critical(
                "AUDIT-MISMATCH: force_password_change audit log write "
                "failed for admin %s -> %s (no transaction support; "
                "UPDATE refused): %s",
                acting_admin_id,
                target_id,
                audit_exc,
            )
            return False, f"Audit log failed: {audit_exc}"

    db.execute(
        "UPDATE admin_users SET force_password_change = 1 WHERE id = ?",
        (target_id,),
    )

    # Transactional path: emit the audit under the same transaction
    # and rollback on any failure so the UPDATE and the audit row
    # stay synchronised.
    if rollback_supported:
        try:
            log_admin_action(
                db,
                acting_admin_id,
                "force_password_change",
                "admin_user",
                target_id,
                {"message": f"Forced password change for admin {target_id}"},
                client_ip or "unknown",
            )
        except Exception as audit_exc:
            logger.critical(
                "AUDIT-MISMATCH: force_password_change audit log write "
                "failed for admin %s -> %s; rolling back: %s",
                acting_admin_id,
                target_id,
                audit_exc,
            )
            try:
                db.rollback()
            except Exception as rb_exc:  # pragma: no cover
                logger.critical(
                    "AUDIT-MISMATCH: rollback after failed audit also "
                    "failed for admin %s: %s",
                    target_id,
                    rb_exc,
                )
            return False, f"Audit log failed: {audit_exc}"
        try:
            db.commit()
        except Exception as commit_exc:  # pragma: no cover
            logger.critical(
                "force_password_change: commit failed for admin %s: %s",
                target_id,
                commit_exc,
            )
            return False, "Could not persist password change"

    return True, "Password change forced successfully"
