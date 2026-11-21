"""
OTP setup management mixin providing begin_otp_setup, disable_otp, and regenerate_backup_codes.
"""

import json
import secrets
import time
from typing import Any, List, Tuple

import pyotp

from .dataclasses import AdminLoginResult
from .helpers import _create_otp_challenge, _verify_admin_password
from .otp_mixin import _check_admin_otp_budget, _is_canonical_totp_code
from src.core.security.secrets import constant_time_compare


class OTPSetupMixin:
    _db: Any

    """OTP lifecycle management (setup, disable, backup codes)."""

    def begin_otp_setup(self, admin_id: int, current_password: str) -> AdminLoginResult:
        """Start a new admin OTP setup flow after password verification."""
        valid, row, message = _verify_admin_password(
            self._db, admin_id, current_password
        )
        if not valid or row is None:
            return AdminLoginResult(success=False, error=message)

        from src.utils.encryption import encrypt_data as _encrypt

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        username = row["username"] if isinstance(row, dict) else row[0]
        qr_uri = totp.provisioning_uri(name=username, issuer_name="Plexichat Admin")
        encrypted_secret = _encrypt(secret, context=f"admin_totp:{admin_id}")
        self._db.execute(
            """
            UPDATE admin_users
            SET totp_secret = NULL, totp_secret_encrypted = ?, totp_enabled = 0, must_setup_otp = 1,
                backup_codes = NULL, backup_codes_hash = NULL
            WHERE id = ?
            """,
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

    def disable_otp(
        self, admin_id: int, current_password: str, code: str
    ) -> Tuple[bool, str]:
        """Disable OTP for the current admin after password and OTP verification."""
        valid, row, message = _verify_admin_password(
            self._db, admin_id, current_password
        )
        if not valid or row is None:
            return False, message

        from .helpers import _check_otp_attempt_limit

        if not _check_otp_attempt_limit(f"disable:{admin_id}"):
            return False, "Too many OTP attempts"
        # Keep the disable flow on the same account-wide budget as login/setup.
        # Without this, an attacker with the password could alternate between
        # verify-otp and disable-otp and double their guessing allowance.
        if not _check_admin_otp_budget(admin_id):
            return False, "Too many OTP attempts"

        # The password-verification read above already selected every OTP
        # column we need (totp_secret, totp_secret_encrypted, totp_enabled,
        # backup_codes, backup_codes_hash). Reuse that row instead of paying
        # for a second admin_users round trip.
        secret_row = row
        if isinstance(secret_row, dict):
            encrypted_secret = secret_row.get("totp_secret_encrypted")
            plaintext_secret = secret_row.get("totp_secret")
            totp_enabled = bool(secret_row["totp_enabled"])
            backup_codes_plaintext = secret_row.get("backup_codes") or ""
            backup_codes_hashed = secret_row.get("backup_codes_hash")
        else:
            # Defensive: _verify_admin_password normalizes to a dict, but keep
            # tuple access aligned with its SELECT column order if that ever
            # changes back.
            totp_enabled = bool(secret_row[5]) if len(secret_row) > 5 else False
            backup_codes_plaintext = secret_row[7] if len(secret_row) > 7 else ""
            plaintext_secret = secret_row[8] if len(secret_row) > 8 else None
            encrypted_secret = secret_row[9] if len(secret_row) > 9 else None
            backup_codes_hashed = secret_row[10] if len(secret_row) > 10 else None

        secret = None
        if encrypted_secret:
            try:
                from src.utils.encryption import decrypt_data as _decrypt

                secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
            except Exception:
                # A decryption failure must not downgrade OTP to a plaintext
                # secret stored for legacy compatibility.
                return False, "OTP unavailable"
        else:
            secret = plaintext_secret

        if not totp_enabled or not secret:
            return False, "OTP is not enabled"

        import hashlib
        from src.utils.encryption import verify_password as _verify_pwd

        normalized = code.upper().replace("-", "")
        candidate_sha256 = hashlib.sha256(normalized.lower().encode()).hexdigest()
        verified = False
        verified_backup_index = None

        # TOTP disable must honor the same single-use replay rule as login.
        # pyotp NFKC-normalises its input, so a fullwidth homoglyph of a code
        # already consumed via login/setup verifies here while the raw-code
        # single-use CAS below no longer matches it. Pin TOTP codes to the
        # canonical ASCII grammar (same gate as verify_otp/verify_otp_setup);
        # backup codes keep their separate XXXX-XXXX hex branch below.
        if _is_canonical_totp_code(code) and pyotp.TOTP(secret).verify(
            code, valid_window=1
        ):
            now_ms = int(time.time() * 1000)
            cursor = self._db.execute(
                "UPDATE admin_users SET otp_last_used_code = ?, otp_last_used_at = ? "
                "WHERE id = ? AND (otp_last_used_code IS NULL OR otp_last_used_code <> ?)",
                (code, now_ms, admin_id, code),
            )
            if getattr(cursor, "rowcount", 0) != 1:
                return False, "Code already used — wait for next code"
            verified = True

        if not verified:
            if backup_codes_hashed:
                try:
                    hashed_list = json.loads(backup_codes_hashed)
                    for i, h in enumerate(hashed_list):
                        h_str = str(h)
                        # Backup-code digests are secret-derived values: route
                        # the comparison through the constant-time helper, as
                        # verify_otp() does. A plain ``==`` leaks the stored
                        # digest through comparison timing.
                        if constant_time_compare(
                            candidate_sha256, h_str
                        ) or _verify_pwd(normalized.lower(), h_str):
                            verified_backup_index = i
                            verified = True
                            break
                except (json.JSONDecodeError, TypeError):
                    pass
            if not verified and backup_codes_plaintext:
                codes = [
                    item for item in str(backup_codes_plaintext).split(",") if item
                ]
                # Legacy plaintext codes are secrets too. Scan the whole list
                # with a constant-time comparator instead of ``in``/``index``,
                # which short-circuit and leak the value and index by timing.
                matched = -1
                for i, stored in enumerate(codes):
                    if constant_time_compare(normalized, stored) and matched == -1:
                        matched = i
                if matched != -1:
                    verified_backup_index = matched
                    verified = True
        if not verified:
            return False, "Invalid OTP or backup code"

        if verified_backup_index is not None and backup_codes_hashed:
            try:
                hashed_list = json.loads(backup_codes_hashed)
            except (json.JSONDecodeError, TypeError):
                hashed_list = []
            if verified_backup_index >= len(hashed_list):
                return False, "Invalid OTP or backup code"
            remaining = (
                hashed_list[:verified_backup_index]
                + hashed_list[verified_backup_index + 1 :]
            )
            cursor = self._db.execute(
                "UPDATE admin_users SET backup_codes_hash = ? WHERE id = ? AND backup_codes_hash = ?",
                (json.dumps(remaining), admin_id, backup_codes_hashed),
            )
            if getattr(cursor, "rowcount", 0) != 1:
                return False, "Invalid OTP or backup code"
        elif verified_backup_index is not None and backup_codes_plaintext:
            codes = [item for item in str(backup_codes_plaintext).split(",") if item]
            if verified_backup_index >= len(codes):
                return False, "Invalid OTP or backup code"
            codes.pop(verified_backup_index)
            cursor = self._db.execute(
                "UPDATE admin_users SET backup_codes = ?, backup_codes_hash = NULL "
                "WHERE id = ? AND backup_codes = ?",
                (
                    (",").join(codes) if codes else None,
                    admin_id,
                    backup_codes_plaintext,
                ),
            )
            if getattr(cursor, "rowcount", 0) != 1:
                return False, "Invalid OTP or backup code"

        self._db.execute(
            """
            UPDATE admin_users
            SET totp_enabled = 0, totp_secret = NULL, totp_secret_encrypted = NULL,
                backup_codes = NULL, backup_codes_hash = NULL, must_setup_otp = 1
            WHERE id = ?
            """,
            (admin_id,),
        )
        return True, "OTP disabled"

    def regenerate_backup_codes(
        self, admin_id: int, current_password: str
    ) -> Tuple[bool, List[str], str]:
        """Regenerate admin backup codes after password verification."""
        valid, row, message = _verify_admin_password(
            self._db, admin_id, current_password
        )
        if not valid or row is None:
            return False, [], message

        # _verify_admin_password already selected totp_enabled on the row we
        # just verified the password against; a second read of the same value
        # was pure overhead on this hot path.
        totp_enabled = (
            bool(row["totp_enabled"]) if isinstance(row, dict) else bool(row[5])
        )
        if not totp_enabled:
            return False, [], "Enable OTP before generating backup codes"

        from src.core.auth.totp import hash_backup_codes as _hash_backup_codes

        backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
        hashed_codes = _hash_backup_codes(backup_codes)
        self._db.execute(
            "UPDATE admin_users SET backup_codes = NULL, backup_codes_hash = ? WHERE id = ?",
            (json.dumps(hashed_codes), admin_id),
        )
        return True, backup_codes, "Backup codes regenerated"
