"""
Authentication mixin providing ensure_admin_user and authenticate_admin.
"""

import time

import utils.config as config

from typing import Any

from .dataclasses import AdminLoginResult
from .helpers import (
    _check_rate_limit,
    _create_otp_challenge,
    _generate_password,
    _save_admin_credentials,
    _sync_password_to_auth_users,
)
from .session_mixin import SessionMixin
from .password_mixin import PasswordMixin


def _stored_hash_is_argon2(password_hash: Any) -> bool:
    """True when a stored admin credential is an Argon2 hash.

    A peppered credential is an envelope around the PHC string
    (``$plexichat$pepper-v1$pid=v1$$argon2id$...``), so the algorithm has to be
    read from the inner hash. Comparing the envelope itself rejected every
    peppered admin credential: admin login answered ``Insecure password hash on
    file; contact operator`` for a valid password on any build that hashes with
    a pepper (found on the live a.1.0-166 deployment).
    """
    if not isinstance(password_hash, str) or not password_hash:
        return False
    try:
        from src.utils.encryption.pepper import parse_envelope

        envelope = parse_envelope(password_hash)
    except Exception:
        return False
    inner = envelope[1] if envelope else password_hash
    return inner.startswith("$argon2")


class AuthenticationMixin(SessionMixin, PasswordMixin):
    _db: Any

    """Authentication and user provisioning."""

    def ensure_admin_user(self) -> None:
        """Ensure admin user exists, create with random password if not."""
        db = self._db
        row = db.fetch_one("SELECT id FROM admin_users WHERE username = ?", ("admin",))
        if row:
            return
        password = _generate_password()
        import src.utils.encryption as encryption

        password_hash = encryption.hash_password(password)
        admin_id = encryption.generate_snowflake_id()
        now = int(time.time())
        db.execute(
            """INSERT INTO admin_users (id, username, password_hash, email, created_at, must_setup_otp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (admin_id, "admin", password_hash, "admin@example.com", now, 1),
        )
        _sync_password_to_auth_users(db, "admin", password_hash)
        _save_admin_credentials(password)

        _seed_default_admin_roles(db, admin_id, now)

        import utils.logger as logger

        logger.info("Created admin user with random password using Argon2id")

    def authenticate_admin(
        self, username: str, password: str, ip: str = "unknown"
    ) -> AdminLoginResult:
        """Authenticate admin user with password and optional OTP."""
        from src.core.database import reset_rate_limit

        admin_config = config.get("admin_ui", {})
        rate_config = admin_config.get("rate_limit", {})
        allowed, wait_seconds = _check_rate_limit(
            ip,
            rate_config.get("max_attempts", 5),
            rate_config.get("window_seconds", 300),
            rate_config.get("lockout_seconds", 900),
        )
        if not allowed:
            return AdminLoginResult(
                success=False,
                error=f"Too many login attempts. Try again in {wait_seconds} seconds.",
                rate_limited=True,
            )

        db = self._db
        row = db.fetch_one(
            "SELECT id, password_hash, totp_secret, totp_secret_encrypted, "
            "totp_enabled, must_setup_otp FROM admin_users WHERE username = ?",
            (username,),
        )
        if not row:
            return AdminLoginResult(success=False, error="Invalid credentials")

        if isinstance(row, dict):
            admin_id = row["id"]
            password_hash = row["password_hash"]
            totp_enabled = bool(row.get("totp_enabled", 0))
            must_setup_otp = bool(row.get("must_setup_otp", 0))
        else:
            admin_id = row[0]
            password_hash = row[1]
            totp_enabled = bool(row[4]) if len(row) > 4 else False
            must_setup_otp = bool(row[5]) if len(row) > 5 else False

        import src.utils.encryption as encryption

        # Passwords stored outside Argon2 are legacy credentials and are not
        # accepted. This prevents a leaked unsalted digest from becoming a
        # usable login credential. A peppered hash stores its Argon2 PHC inside
        # the envelope, so the check unwraps it first (see
        # _stored_hash_is_argon2).
        if not _stored_hash_is_argon2(password_hash):
            import utils.logger as auth_logger

            auth_logger.critical(
                "Refusing admin login for %s: password hash is not Argon2id",
                admin_id,
            )
            return AdminLoginResult(
                success=False,
                error="Insecure password hash on file; contact operator",
            )

        if not encryption.verify_password(password, password_hash):
            return AdminLoginResult(success=False, error="Invalid credentials")

        # A successful password phase clears the shared failure bucket. OTP
        # verification has its own challenge-bound attempt budget.
        reset_rate_limit(f"admin_login:{ip}")

        otp_required = admin_config.get("require_otp", True)
        if not otp_required:
            if must_setup_otp:
                db.execute(
                    "UPDATE admin_users SET must_setup_otp = 0 WHERE id = ?",
                    (admin_id,),
                )
            token = self.create_session(admin_id)
            db.execute(
                "UPDATE admin_users SET last_login = ? WHERE id = ?",
                (int(time.time() * 1000), admin_id),
            )
            return AdminLoginResult(
                success=True,
                token=token,
                user_id=admin_id,
                requires_password_change=self.check_password_change_required(admin_id),
            )

        if must_setup_otp or not totp_enabled:
            import pyotp
            from src.utils.encryption import encrypt_data as _encrypt

            secret = pyotp.random_base32()
            qr_uri = pyotp.TOTP(secret).provisioning_uri(
                name=username, issuer_name="Plexichat Admin"
            )
            encrypted_secret = _encrypt(secret, context=f"admin_totp:{admin_id}")
            db.execute(
                "UPDATE admin_users SET totp_secret_encrypted = ?, totp_secret = NULL, "
                "must_setup_otp = 1 WHERE id = ?",
                (encrypted_secret, admin_id),
            )
            return AdminLoginResult(
                success=True,
                user_id=admin_id,
                requires_otp_setup=True,
                otp_secret=secret,
                otp_qr_uri=qr_uri,
                challenge_token=_create_otp_challenge(admin_id, is_setup=True),
            )

        return AdminLoginResult(
            success=True,
            user_id=admin_id,
            requires_otp_verify=True,
            challenge_token=_create_otp_challenge(admin_id, is_setup=False),
        )


def _seed_default_admin_roles(db, admin_id: int, now: int) -> None:
    """Create the system super-admin role and a delegable standard role."""
    import json
    import utils.logger as logger
    from src.utils.encryption import generate_snowflake_id

    existing = db.fetch_one("SELECT COUNT(*) as c FROM admin_roles")
    count = (
        existing.get("c", 0)
        if isinstance(existing, dict)
        else (existing[0] if existing else 0)
    )

    if not count:
        super_admin_id = generate_snowflake_id()
        db.execute(
            """INSERT INTO admin_roles
               (id, name, description, permissions, created_at, created_by, is_system, position)
               VALUES (?, 'super_admin', 'Full system access with all permissions', ?, ?, ?, 1, 100)""",
            (super_admin_id, json.dumps({"*": True}), now, admin_id),
        )
        db.execute(
            """INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by)
               VALUES (?, ?, ?, ?)""",
            (admin_id, super_admin_id, now, admin_id),
        )
        count = 1

    # Keep a non-system role available for delegated administrators. Existing
    # installations may have only the original super_admin seed.
    standard = db.fetch_one(
        "SELECT id FROM admin_roles WHERE name = ?", ("support_admin",)
    )
    if not standard:
        standard_id = generate_snowflake_id()
        db.execute(
            """INSERT INTO admin_roles
               (id, name, description, permissions, created_at, created_by, is_system, position)
               VALUES (?, 'support_admin', 'Limited administrative access', ?, ?, ?, 0, 10)""",
            (
                standard_id,
                json.dumps({"admin.read": True, "admin.edit": True}),
                now,
                admin_id,
            ),
        )

    if not count:
        logger.info(f"Created default admin roles for admin {admin_id}")
