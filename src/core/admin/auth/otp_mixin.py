"""
OTP verification mixin providing verify_otp_setup and verify_otp.
"""

import json
import secrets
import time

import pyotp

from typing import Any

from .dataclasses import AdminLoginResult
from .helpers import (
    _consume_otp_challenge,
    _validate_otp_challenge,
)
from .session_mixin import SessionMixin
from .password_mixin import PasswordMixin


class OTPMixin(SessionMixin, PasswordMixin):
    _db: Any

    """OTP setup and verification."""

    def verify_otp_setup(
        self, admin_id: int, code: str, challenge_token: str
    ) -> AdminLoginResult:
        """Verify OTP code during setup."""
        if not _validate_otp_challenge(challenge_token, admin_id, is_setup=True):
            return AdminLoginResult(
                success=False, error="Invalid or expired OTP challenge"
            )

        db = self._db
        row = db.fetch_one(
            "SELECT totp_secret, totp_secret_encrypted FROM admin_users WHERE id = ?",
            (admin_id,),
        )
        if not row:
            return AdminLoginResult(success=False, error="Admin user not found")
        encrypted_secret = (
            row.get("totp_secret_encrypted")
            if isinstance(row, dict)
            else (row[1] if len(row) > 1 else None)
        )
        plaintext_secret = row.get("totp_secret") if isinstance(row, dict) else row[0]
        secret = None
        if encrypted_secret:
            try:
                from src.utils.encryption import decrypt_data as _decrypt

                secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
            except Exception:
                secret = plaintext_secret
        else:
            secret = plaintext_secret
        if not secret:
            return AdminLoginResult(success=False, error="OTP not configured")

        last_row = db.fetch_one(
            "SELECT otp_last_used_code, otp_last_used_at FROM admin_users WHERE id = ?",
            (admin_id,),
        )
        if last_row:
            last_code = (
                last_row.get("otp_last_used_code")
                if isinstance(last_row, dict)
                else last_row[0]
            )
            if last_code == code:
                return AdminLoginResult(
                    success=False, error="Code already used — wait for next code"
                )

        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            return AdminLoginResult(success=False, error="Invalid OTP code")

        db.execute(
            "UPDATE admin_users SET otp_last_used_code = ?, otp_last_used_at = ? WHERE id = ?",
            (code, int(time.time() * 1000), admin_id),
        )
        db.execute(
            "UPDATE admin_users SET totp_enabled = 1, must_setup_otp = 0 WHERE id = ?",
            (admin_id,),
        )
        from src.core.auth.totp import hash_backup_codes as _hash_backup_codes

        backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
        hashed_codes = _hash_backup_codes(backup_codes)
        db.execute(
            "UPDATE admin_users SET backup_codes = NULL, backup_codes_hash = ? WHERE id = ?",
            (json.dumps(hashed_codes), admin_id),
        )
        _consume_otp_challenge(challenge_token)

        requires_password_change = self.check_password_change_required(admin_id)

        return AdminLoginResult(
            success=True,
            token=self.create_session(admin_id),
            user_id=admin_id,
            requires_password_change=requires_password_change,
        )

    def verify_otp(
        self, admin_id: int, code: str, challenge_token: str
    ) -> AdminLoginResult:
        """Verify OTP code for login."""
        if not _validate_otp_challenge(challenge_token, admin_id, is_setup=False):
            return AdminLoginResult(
                success=False, error="Invalid or expired OTP challenge"
            )

        db = self._db
        row = db.fetch_one(
            "SELECT totp_secret, totp_secret_encrypted, backup_codes, backup_codes_hash FROM admin_users WHERE id = ?",
            (admin_id,),
        )
        if not row:
            return AdminLoginResult(success=False, error="Admin user not found")
        if isinstance(row, dict):
            encrypted_secret = row.get("totp_secret_encrypted")
            plaintext_secret = row.get("totp_secret")
            backup_codes_plaintext = row.get("backup_codes")
            backup_codes_hashed = row.get("backup_codes_hash")
        else:
            encrypted_secret = row[1] if len(row) > 1 else None
            plaintext_secret = row[0]
            backup_codes_plaintext = row[2] if len(row) > 2 else None
            backup_codes_hashed = row[3] if len(row) > 3 else None

        secret = None
        if encrypted_secret:
            try:
                from src.utils.encryption import decrypt_data as _decrypt

                secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
            except Exception:
                secret = plaintext_secret
        else:
            secret = plaintext_secret
        if not secret:
            return AdminLoginResult(success=False, error="OTP not configured")

        last_row = db.fetch_one(
            "SELECT otp_last_used_code, otp_last_used_at FROM admin_users WHERE id = ?",
            (admin_id,),
        )
        if last_row:
            last_code = (
                last_row.get("otp_last_used_code")
                if isinstance(last_row, dict)
                else last_row[0]
            )
            if last_code == code:
                return AdminLoginResult(
                    success=False, error="Code already used — wait for next code"
                )

        if pyotp.TOTP(secret).verify(code, valid_window=1):
            db.execute(
                "UPDATE admin_users SET otp_last_used_code = ?, otp_last_used_at = ?, last_login = ? WHERE id = ?",
                (code, int(time.time() * 1000), int(time.time() * 1000), admin_id),
            )
            _consume_otp_challenge(challenge_token)

            requires_password_change = self.check_password_change_required(admin_id)

            return AdminLoginResult(
                success=True,
                token=self.create_session(admin_id),
                user_id=admin_id,
                requires_password_change=requires_password_change,
            )

        import hashlib
        from src.utils.encryption import verify_password as _verify_pwd

        normalized = code.upper().replace("-", "")
        candidate_sha256 = hashlib.sha256(normalized.lower().encode()).hexdigest()
        if backup_codes_hashed:
            try:
                hashed_list = json.loads(backup_codes_hashed)
            except (json.JSONDecodeError, TypeError):
                hashed_list = []
            for i, hashed in enumerate(hashed_list):
                hashed_str = str(hashed)
                if candidate_sha256 == hashed_str or _verify_pwd(
                    normalized.lower(), hashed_str
                ):
                    hashed_list.pop(i)
                    db.execute(
                        "UPDATE admin_users SET backup_codes_hash = ?, last_login = ? WHERE id = ?",
                        (json.dumps(hashed_list), int(time.time() * 1000), admin_id),
                    )
                    _consume_otp_challenge(challenge_token)

                    requires_password_change = self.check_password_change_required(
                        admin_id
                    )

                    return AdminLoginResult(
                        success=True,
                        token=self.create_session(admin_id),
                        user_id=admin_id,
                        requires_password_change=requires_password_change,
                    )
        elif backup_codes_plaintext:
            codes = backup_codes_plaintext.split(",")
            if normalized in codes:
                codes.remove(normalized)
                hashed_remaining = [
                    hashlib.sha256(c.lower().encode()).hexdigest()
                    for c in codes
                    if c.strip()
                ]
                db.execute(
                    "UPDATE admin_users SET backup_codes = NULL, backup_codes_hash = ?, last_login = ? WHERE id = ?",
                    (json.dumps(hashed_remaining), int(time.time() * 1000), admin_id),
                )
                _consume_otp_challenge(challenge_token)

                requires_password_change = self.check_password_change_required(admin_id)

                return AdminLoginResult(
                    success=True,
                    token=self.create_session(admin_id),
                    user_id=admin_id,
                    requires_password_change=requires_password_change,
                )
        return AdminLoginResult(success=False, error="Invalid OTP code")
