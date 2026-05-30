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
        import utils.logger as logger

        logger.info("Created admin user with random password using Argon2id")

    def authenticate_admin(
        self, username: str, password: str, ip: str = "unknown"
    ) -> AdminLoginResult:
        """Authenticate admin user."""
        import time
        from src.core.database import cache_get, cache_set, cache_delete

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
            "SELECT id, password_hash, totp_secret, totp_secret_encrypted, totp_enabled, must_setup_otp FROM admin_users WHERE username = ?",
            (username,),
        )
        if not row:
            attempts_key = f"admin_login_attempts:{ip}"
            attempts = cache_get(attempts_key) or []
            attempts.append(time.time() * 1000)
            cache_set(
                attempts_key, attempts, ttl=rate_config.get("window_seconds", 300)
            )
            return AdminLoginResult(success=False, error="Invalid credentials")

        if isinstance(row, dict):
            admin_id = row["id"]
            password_hash = row["password_hash"]
            totp_enabled = bool(row["totp_enabled"])
            must_setup_otp = bool(row["must_setup_otp"])
        else:
            admin_id = row[0]
            password_hash = row[1]
            _totp_secret = (
                row[3]
                if len(row) > 3 and row[3]
                else (row[2] if len(row) > 2 else None)
            )
            totp_enabled = bool(row[4]) if len(row) > 4 else False
            must_setup_otp = bool(row[5]) if len(row) > 5 else False

        import src.utils.encryption as encryption

        authenticated = False
        if not password_hash.startswith("$argon2"):
            import hashlib

            if hashlib.sha256(password.encode()).hexdigest() == password_hash:
                authenticated = True
                new_hash = encryption.hash_password(password)
                db.execute(
                    "UPDATE admin_users SET password_hash = ? WHERE id = ?",
                    (new_hash, admin_id),
                )
                _sync_password_to_auth_users(db, username, new_hash)
        else:
            if encryption.verify_password(password, password_hash):
                authenticated = True

        if not authenticated:
            attempts_key = f"admin_login_attempts:{ip}"
            attempts = cache_get(attempts_key) or []
            attempts.append(time.time() * 1000)
            cache_set(
                attempts_key, attempts, ttl=rate_config.get("window_seconds", 300)
            )
            return AdminLoginResult(success=False, error="Invalid credentials")

        cache_delete(f"admin_login_attempts:{ip}")
        cache_delete(f"admin_login_lockout:{ip}")

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

            requires_password_change = self.check_password_change_required(admin_id)

            return AdminLoginResult(
                success=True,
                token=token,
                user_id=admin_id,
                requires_password_change=requires_password_change,
            )

        if must_setup_otp or not totp_enabled:
            import pyotp
            from src.utils.encryption import encrypt_data as _encrypt

            secret = pyotp.random_base32()
            totp = pyotp.TOTP(secret)
            qr_uri = totp.provisioning_uri(name=username, issuer_name="Plexichat Admin")
            encrypted_secret = _encrypt(secret, context=f"admin_totp:{admin_id}")
            db.execute(
                "UPDATE admin_users SET totp_secret_encrypted = ?, totp_secret = NULL, must_setup_otp = 1 WHERE id = ?",
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

        if totp_enabled:
            return AdminLoginResult(
                success=True,
                user_id=admin_id,
                requires_otp_verify=True,
                challenge_token=_create_otp_challenge(admin_id, is_setup=False),
            )

        return AdminLoginResult(
            success=True,
            user_id=admin_id,
            requires_otp_verify=True,
            challenge_token=_create_otp_challenge(admin_id, is_setup=False),
        )
