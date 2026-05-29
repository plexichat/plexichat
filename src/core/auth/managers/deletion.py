import time
import hashlib
from typing import Optional

import utils.logger as logger
from src.core.database import invalidate_pattern

from ..exceptions import (
    UserNotFoundError,
    InvalidCredentialsError,
    TwoFactorInvalidError,
)
from .. import totp as totp_module


from .protocol import AuthManagerProtocol


class DeletionMixin(AuthManagerProtocol):
    def schedule_account_deletion(
        self, user_id: int, password: str, totp_code: Optional[str] = None
    ) -> bool:
        row = self._db.fetch_one(
            "SELECT username, email_encrypted, password_hash, totp_enabled, totp_secret_encrypted FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not row:
            raise UserNotFoundError("User not found")

        if not self.crypto.verify_password(password, row["password_hash"]):
            raise InvalidCredentialsError("Incorrect password")

        if row["totp_enabled"]:
            if not totp_code:
                raise TwoFactorInvalidError("2FA code required")

            secret = self.crypto.decrypt_data(
                row["totp_secret_encrypted"], context=str(user_id)
            )
            if not totp_module.verify_totp_code(secret, totp_code, user_id=user_id):
                raise TwoFactorInvalidError("Invalid 2FA code")

        grace_days = self._config.get("account_deletion", {}).get(
            "grace_period_days", 30
        )
        now = self._get_timestamp()
        deletion_at = now + (grace_days * 86400)

        self._db.execute(
            "UPDATE auth_users SET deletion_status = 'frozen', deletion_at = ? WHERE id = ?",
            (deletion_at, user_id),
        )

        record_id = self._generate_id()
        identifier = row["username"]

        self._db.execute(
            "INSERT INTO auth_deletion_records (id, user_id, identifier_hash, status, scheduled_at) VALUES (?, ?, ?, ?, ?)",
            (
                record_id,
                user_id,
                hashlib.sha256(identifier.encode()).hexdigest(),
                "frozen",
                now,
            ),
        )

        self.deletion_log.log_event(
            user_id,
            "SCHEDULED",
            identifier,
            {"scheduled_at": now, "deletion_at": deletion_at},
        )

        self.logout_all(user_id)

        invalidate_pattern(f"user_profile:{user_id}")
        invalidate_pattern(f"user_data:*{user_id}*")

        logger.info(
            f"Account scheduled for deletion: user_id={user_id}, scheduled_at={now}, deletion_at={deletion_at}"
        )
        return True

    def cancel_account_deletion(
        self, user_id: int, admin_id: Optional[int] = None
    ) -> bool:
        row = self._db.fetch_one(
            "SELECT username FROM auth_users WHERE id = ?", (user_id,)
        )
        if not row:
            raise UserNotFoundError("User not found")

        self._db.execute(
            "UPDATE auth_users SET deletion_status = 'active', deletion_at = NULL WHERE id = ?",
            (user_id,),
        )

        self._db.execute(
            "DELETE FROM auth_deletion_records WHERE user_id = ?", (user_id,)
        )

        self.deletion_log.log_event(
            user_id, "CANCELLED", row["username"], {"admin_id": admin_id}
        )

        invalidate_pattern(f"user_profile:{user_id}")
        logger.info(
            f"Account deletion cancelled: user_id={user_id}, cancelled_by={admin_id or 'user'}"
        )
        return True

    def delay_account_deletion(
        self, user_id: int, deletion_at: int, admin_id: Optional[int] = None
    ) -> bool:
        row = self._db.fetch_one(
            "SELECT username, deletion_status FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not row:
            raise UserNotFoundError("User not found")

        if row["deletion_status"] != "frozen":
            raise ValueError("Account is not scheduled for deletion")

        logger.info(f"Delay deletion: user_id={user_id}, deletion_at={deletion_at}")

        self._db.execute(
            "UPDATE auth_users SET deletion_at = ? WHERE id = ?",
            (deletion_at, user_id),
        )

        self.deletion_log.log_event(
            user_id,
            "DELAYED",
            row["username"],
            {
                "admin_id": admin_id,
                "deletion_at": deletion_at,
            },
        )

        invalidate_pattern(f"user_profile:{user_id}")
        logger.info(
            f"Account deletion delayed: user_id={user_id}, deletion_at={deletion_at}, delayed_by={admin_id or 'user'}"
        )
        return True

    def force_purge_account(self, user_id: int, admin_id: Optional[int] = None) -> bool:
        row = self._db.fetch_one(
            "SELECT username, deletion_status FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not row:
            raise UserNotFoundError("User not found")

        self.deletion_log.log_event(
            user_id,
            "FORCE_PURGED",
            row["username"],
            {"admin_id": admin_id, "reason": "Admin force purge"},
        )

        from src.core.auth.reaper import AccountReaper

        reaper = AccountReaper(self._db, self._config.get("account_deletion", {}))
        reaper.purge_user(user_id, row["username"])

        self._db.execute(
            "UPDATE auth_users SET deletion_status = 'purged', deletion_at = ? WHERE id = ?",
            (int(time.time()), user_id),
        )

        self._db.execute(
            "DELETE FROM auth_deletion_records WHERE user_id = ?", (user_id,)
        )

        invalidate_pattern(f"user_profile:{user_id}")
        invalidate_pattern(f"user_data:*{user_id}*")
        logger.warning(
            f"Account force purged: user_id={user_id}, purged_by={admin_id or 'system'}"
        )
        return True
