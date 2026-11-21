"""
OTP verification mixin providing verify_otp_setup and verify_otp.
"""

import json
import re
import secrets
import time

import pyotp

from typing import Any

from .dataclasses import AdminLoginResult
from .helpers import (
    _consume_otp_challenge,
    _validate_otp_challenge,
    _check_otp_attempt_limit,
    _otp_challenge_fingerprint,
)
from .session_mixin import SessionMixin
from .password_mixin import PasswordMixin
from src.core.security.secrets import constant_time_compare


# A correct password mints a fresh, single-use OTP challenge on every login,
# and the successful password phase resets the IP login bucket. Bounding
# guesses per challenge alone therefore does not bound guesses per account:
# an attacker holding only the password can request unlimited challenges and
# try one code against each. Every OTP verification entry point (setup, login,
# and disable) shares this account-scoped budget so rotating challenges or
# switching flows cannot extend it.
ADMIN_OTP_MAX_ATTEMPTS = 5
ADMIN_OTP_WINDOW_SECONDS = 900

# pyotp's verifier NFKC-normalises its input before comparing, so a fullwidth
# spelling of a TOTP code (e.g. "１２３４５６") verifies for the same secret as
# the ASCII spelling. The single-use replay guard stores and compares the raw
# presented code, so a homoglyph of an already-consumed code skipped both the
# reuse pre-check and the conditional claim and minted a second session.
# Pin TOTP codes to the canonical ASCII grammar the authenticator emits, as
# ``src.core.auth.totp.verify_totp_code`` already does for user-facing 2FA.
# Backup codes are handled by the separate branch below and keep their
# XXXX-XXXX hex grammar.
_ASCII_DIGITS_RE = re.compile(r"[0-9]+")


def _is_canonical_totp_code(code: Any) -> bool:
    """Whether ``code`` is an ASCII-digit TOTP code (fail closed otherwise)."""
    return isinstance(code, str) and bool(_ASCII_DIGITS_RE.fullmatch(code))


def _check_admin_otp_budget(admin_id: int) -> bool:
    """Consume one account-wide OTP guess, independent of the challenge token."""
    return _check_otp_attempt_limit(
        f"admin_otp_account:{admin_id}",
        max_attempts=ADMIN_OTP_MAX_ATTEMPTS,
        window_seconds=ADMIN_OTP_WINDOW_SECONDS,
    )


def _same_otp_code(last_code: Any, presented_code: Any) -> bool:
    """Whether a presented OTP equals the code recorded as last used.

    The last-used code is still a live secret for the length of TOTP's
    ``valid_window=1`` (±30s): it can authenticate until its window closes,
    which is exactly why the reuse guard exists. Comparing it with ``==``
    short-circuits at the first differing character and leaks a prefix oracle
    to a timing observer. Route string comparisons through the shared
    constant-time helper, matching the backup-code path below. Non-string
    values keep their original equality semantics (fail-closed for None).
    """
    if isinstance(last_code, str) and isinstance(presented_code, str):
        return constant_time_compare(last_code, presented_code)
    return last_code == presented_code


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
        if not isinstance(code, str):
            return AdminLoginResult(success=False, error="Invalid OTP code")
        # Account-scoped budget first: a new challenge must not restore the
        # guesses already spent against this admin in another flow.
        if not _check_admin_otp_budget(admin_id):
            _consume_otp_challenge(challenge_token)
            return AdminLoginResult(success=False, error="Too many OTP attempts")
        # Scope the per-challenge budget to a one-way fingerprint: the raw
        # challenge token is a live credential and must never become a
        # rate-limit/cache key visible to cache operators.
        if not _check_otp_attempt_limit(_otp_challenge_fingerprint(challenge_token)):
            _consume_otp_challenge(challenge_token)
            return AdminLoginResult(success=False, error="Too many OTP attempts")
        # OTP challenges are single-attempt credentials. Consuming the
        # challenge before verification prevents a failed code from leaving
        # a reusable five-minute guessing window; callers can start a fresh
        # challenge after correcting the code.
        _consume_otp_challenge(challenge_token)

        db = self._db
        # Read the whole admin_users row in one round trip: the TOTP secret and
        # the last-used-code reuse guard live on the same row we already pay to
        # read. Two separate SELECTs per verification were pure overhead on the
        # admin login hot path. The single-use guarantee below is an atomic
        # conditional UPDATE keyed on the presented code -- not on anything
        # read here -- so fetching both columns in one snapshot cannot weaken it.
        row = db.fetch_one(
            "SELECT totp_secret, totp_secret_encrypted, otp_last_used_code "
            "FROM admin_users WHERE id = ?",
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
        last_code = (
            row.get("otp_last_used_code")
            if isinstance(row, dict)
            else (row[2] if len(row) > 2 else None)
        )
        secret = None
        if encrypted_secret:
            try:
                from src.utils.encryption import decrypt_data as _decrypt

                secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
            except Exception:
                # Do not fall back to a plaintext secret when decryption fails;
                # a crypto failure must fail closed rather than silently
                # downgrading OTP protection.
                return AdminLoginResult(success=False, error="OTP unavailable")
        else:
            secret = plaintext_secret
        if not secret:
            return AdminLoginResult(success=False, error="OTP not configured")

        if _same_otp_code(last_code, code):
            return AdminLoginResult(
                success=False, error="Code already used — wait for next code"
            )

        if not _is_canonical_totp_code(code) or not pyotp.TOTP(secret).verify(
            code, valid_window=1
        ):
            return AdminLoginResult(success=False, error="Invalid OTP code")

        now_ms = int(time.time() * 1000)
        cursor = db.execute(
            "UPDATE admin_users SET otp_last_used_code = ?, otp_last_used_at = ? "
            "WHERE id = ? AND (otp_last_used_code IS NULL OR otp_last_used_code <> ?)",
            (code, now_ms, admin_id, code),
        )
        if getattr(cursor, "rowcount", 0) != 1:
            return AdminLoginResult(
                success=False, error="Code already used — wait for next code"
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
        if not isinstance(code, str):
            return AdminLoginResult(success=False, error="Invalid OTP code")
        # Account-scoped budget first: a fresh challenge obtained by another
        # password phase must not restore the account's exhausted guess budget.
        if not _check_admin_otp_budget(admin_id):
            _consume_otp_challenge(challenge_token)
            return AdminLoginResult(success=False, error="Too many OTP attempts")
        # Scope the per-challenge budget to a one-way fingerprint: the raw
        # challenge token is a live credential and must never become a
        # rate-limit/cache key visible to cache operators.
        if not _check_otp_attempt_limit(_otp_challenge_fingerprint(challenge_token)):
            _consume_otp_challenge(challenge_token)
            return AdminLoginResult(success=False, error="Too many OTP attempts")
        # OTP challenges are single-attempt credentials. Consuming the
        # challenge before verification prevents a failed code from leaving
        # a reusable five-minute guessing window; callers can start a fresh
        # challenge after correcting the code.
        _consume_otp_challenge(challenge_token)

        db = self._db
        # One round trip for the whole admin_users row (see verify_otp_setup).
        # The secret, backup codes and the reuse guard are all columns of the
        # same row; the atomic CAS below is the single-use authority.
        row = db.fetch_one(
            "SELECT totp_secret, totp_secret_encrypted, backup_codes, "
            "backup_codes_hash, otp_last_used_code "
            "FROM admin_users WHERE id = ?",
            (admin_id,),
        )
        if not row:
            return AdminLoginResult(success=False, error="Admin user not found")
        if isinstance(row, dict):
            encrypted_secret = row.get("totp_secret_encrypted")
            plaintext_secret = row.get("totp_secret")
            backup_codes_plaintext = row.get("backup_codes")
            backup_codes_hashed = row.get("backup_codes_hash")
            last_code = row.get("otp_last_used_code")
        else:
            encrypted_secret = row[1] if len(row) > 1 else None
            plaintext_secret = row[0]
            backup_codes_plaintext = row[2] if len(row) > 2 else None
            backup_codes_hashed = row[3] if len(row) > 3 else None
            last_code = row[4] if len(row) > 4 else None

        secret = None
        if encrypted_secret:
            try:
                from src.utils.encryption import decrypt_data as _decrypt

                secret = _decrypt(encrypted_secret, context=f"admin_totp:{admin_id}")
            except Exception:
                # Do not fall back to a plaintext secret when decryption fails;
                # a crypto failure must fail closed rather than silently
                # downgrading OTP protection.
                return AdminLoginResult(success=False, error="OTP unavailable")
        else:
            secret = plaintext_secret
        if not secret:
            return AdminLoginResult(success=False, error="OTP not configured")

        if _same_otp_code(last_code, code):
            return AdminLoginResult(
                success=False, error="Code already used — wait for next code"
            )

        if _is_canonical_totp_code(code) and pyotp.TOTP(secret).verify(
            code, valid_window=1
        ):
            # Single-use claim: the pre-check above is racy (two concurrent
            # logins both read the same "last used" value), so the UPDATE must
            # itself refuse a code another request already consumed. Mirror
            # verify_otp_setup/disable_otp and only issue a session when the
            # claim actually lands.
            now_ms = int(time.time() * 1000)
            cursor = db.execute(
                "UPDATE admin_users SET otp_last_used_code = ?, otp_last_used_at = ?, last_login = ? "
                "WHERE id = ? AND (otp_last_used_code IS NULL OR otp_last_used_code <> ?)",
                (code, now_ms, now_ms, admin_id, code),
            )
            if getattr(cursor, "rowcount", 0) != 1:
                return AdminLoginResult(
                    success=False, error="Code already used — wait for next code"
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
                # Backup-code digests are secret-derived values: compare them
                # in constant time, matching the hardened core TOTP path
                # (``src.core.auth.totp.verify_backup_code``). A plain ``==``
                # leaks the stored digest through comparison timing.
                if constant_time_compare(candidate_sha256, hashed_str) or _verify_pwd(
                    normalized.lower(), hashed_str
                ):
                    hashed_list.pop(i)
                    cursor = db.execute(
                        "UPDATE admin_users SET backup_codes_hash = ?, last_login = ? "
                        "WHERE id = ? AND backup_codes_hash = ?",
                        (
                            json.dumps(hashed_list),
                            int(time.time() * 1000),
                            admin_id,
                            backup_codes_hashed,
                        ),
                    )
                    if getattr(cursor, "rowcount", 0) != 1:
                        return AdminLoginResult(
                            success=False, error="Backup code already used"
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
            # Legacy plaintext backup codes are still secrets: scan the whole
            # list with a constant-time comparator instead of the short-circuit
            # ``in``/``==`` so neither the value nor its position leaks through
            # comparison timing.
            matched = -1
            for i, stored in enumerate(codes):
                hit = constant_time_compare(normalized, stored)
                if hit and matched == -1:
                    matched = i
            if matched != -1:
                codes.pop(matched)
                hashed_remaining = [
                    hashlib.sha256(c.lower().encode()).hexdigest()
                    for c in codes
                    if c.strip()
                ]
                cursor = db.execute(
                    "UPDATE admin_users SET backup_codes = NULL, backup_codes_hash = ?, last_login = ? "
                    "WHERE id = ? AND backup_codes = ?",
                    (
                        json.dumps(hashed_remaining),
                        int(time.time() * 1000),
                        admin_id,
                        backup_codes_plaintext,
                    ),
                )
                if getattr(cursor, "rowcount", 0) != 1:
                    return AdminLoginResult(
                        success=False, error="Backup code already used"
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
